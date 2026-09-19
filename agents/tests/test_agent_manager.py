import base64

import pytest

import orchestrator.agent_manager as am
from orchestrator import ollama_client
from orchestrator.agent_manager import MAX_TOOL_ITERATIONS, SYSTEM_PROMPTS, AgentManager
from tools import registry


class ChatRecorder:
    """Stands in for ollama_client.chat; replays scripted replies and records calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, model, messages, **kw):
        self.calls.append({"model": model, "messages": list(messages), **kw})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return {"message": reply}


def _say(text):
    return {"role": "assistant", "content": text}


def _call(name, args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


class TestModelSelection:
    def test_std_tier_uses_default_tag(self, make_manager):
        m = make_manager("std")
        assert m._model_for("coder")["ollama_tag"] == m.registry["coder"]["ollama_tag"]

    def test_high_tier_switches_to_fallback_tag(self, make_manager):
        m = make_manager("high")
        assert m._model_for("coder")["ollama_tag"] == m.registry["coder"]["fallback_tag"]

    def test_min_tier_never_uses_fallback(self, make_manager):
        m = make_manager("min")
        assert m._model_for("coder")["ollama_tag"] == m.registry["coder"]["ollama_tag"]

    def test_disabled_role_raises(self, make_manager):
        with pytest.raises(RuntimeError, match="disabled"):
            make_manager("min")._model_for("vision")

    def test_unknown_role_raises(self, make_manager):
        with pytest.raises(KeyError):
            make_manager("std")._model_for("nonsense")


class TestPlainRun:
    def test_builds_system_history_user_and_passes_keep_alive(self, make_manager, monkeypatch):
        rec = ChatRecorder(_say("ok"))
        monkeypatch.setattr(ollama_client, "chat", rec)
        m = make_manager()
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        out = m.run("writer_general", "اكتب لي", history=history)

        msgs = rec.calls[0]["messages"]
        assert msgs[0] == {"role": "system", "content": SYSTEM_PROMPTS["writer_general"]}
        assert msgs[1:3] == history and msgs[-1] == {"role": "user", "content": "اكتب لي"}
        assert rec.calls[0]["keep_alive"] == m.registry["writer_general"]["keep_alive"]
        assert out == {"role": "writer_general", "content": "ok", "tool_trace": [], "sources": [], "skills": []}

    def test_rag_role_injects_context_and_reports_sources(self, make_manager, monkeypatch):
        rec = ChatRecorder(_say("answer"))
        monkeypatch.setattr(ollama_client, "chat", rec)
        m = make_manager(hits=[{"text": "Riyadh branch opens at 9", "source": "branch_info.txt", "distance": 0.1}])
        out = m.run("researcher_rag", "when does it open?")
        prompt = rec.calls[0]["messages"][-1]["content"]
        assert "Riyadh branch opens at 9" in prompt and "when does it open?" in prompt
        assert out["sources"] == ["branch_info.txt"]

    def test_rag_with_no_hits_tells_model_not_to_guess(self, make_manager, monkeypatch):
        rec = ChatRecorder(_say("x"))
        monkeypatch.setattr(ollama_client, "chat", rec)
        out = make_manager(hits=[]).run("researcher_rag", "q")
        assert "No matching content" in rec.calls[0]["messages"][-1]["content"]
        assert out["sources"] == []


class TestToolLoop:
    @pytest.fixture
    def fake_tools(self, monkeypatch):
        seen = []

        def echo(**kw):
            seen.append(kw)
            return f"echo:{kw}"

        def broken(**kw):
            raise RuntimeError("kaput")

        monkeypatch.setitem(registry._REGISTRY, "echo", {"fn": echo, "schema": {"type": "function", "function": {
            "name": "echo", "description": "echo everything back snippets", "parameters": {"type": "object", "properties": {}}}}})
        monkeypatch.setitem(registry._REGISTRY, "broken", {"fn": broken, "schema": {"type": "function", "function": {
            "name": "broken", "description": "always fails", "parameters": {"type": "object", "properties": {}}}}})
        return seen

    def test_tool_result_is_fed_back_and_traced(self, make_manager, monkeypatch, fake_tools):
        rec = ChatRecorder(_call("echo", {"x": "1"}), _say("done"))
        monkeypatch.setattr(ollama_client, "chat", rec)
        out = make_manager().run("tool_caller", "use the tool")
        assert out["content"] == "done"
        assert out["tool_trace"] == [{"tool": "echo", "args": {"x": "1"}, "output": "echo:{'x': '1'}"}]
        assert rec.calls[1]["messages"][-1] == {"role": "tool", "content": "echo:{'x': '1'}"}
        assert rec.calls[0]["tools"], "tool schemas must be sent to the model"

    def test_arguments_may_arrive_as_a_json_string(self, make_manager, monkeypatch, fake_tools):
        monkeypatch.setattr(ollama_client, "chat", ChatRecorder(_call("echo", '{"x": "1"}'), _say("ok")))
        make_manager().run("tool_caller", "go")
        assert fake_tools == [{"x": "1"}]

    def test_malformed_json_arguments_do_not_kill_the_turn(self, make_manager, monkeypatch, fake_tools):
        monkeypatch.setattr(ollama_client, "chat", ChatRecorder(_call("echo", "{not json"), _say("recovered")))
        out = make_manager().run("tool_caller", "go")
        assert out["content"] == "recovered"
        assert "Error" in str(out["tool_trace"][0]["output"])

    def test_unknown_tool_is_reported_to_the_model(self, make_manager, monkeypatch, fake_tools):
        monkeypatch.setattr(ollama_client, "chat", ChatRecorder(_call("ghost", {}), _say("ok")))
        out = make_manager().run("tool_caller", "go")
        assert "unknown tool 'ghost'" in out["tool_trace"][0]["output"]

    def test_crashing_tool_is_contained(self, make_manager, monkeypatch, fake_tools):
        monkeypatch.setattr(ollama_client, "chat", ChatRecorder(_call("broken", {}), _say("ok")))
        out = make_manager().run("tool_caller", "go")
        assert "kaput" in out["tool_trace"][0]["output"] and out["content"] == "ok"

    def test_bad_argument_names_are_contained(self, make_manager, monkeypatch, fake_tools):
        monkeypatch.setitem(registry._REGISTRY, "strict", {"fn": lambda a: a, "schema": {"type": "function", "function": {
            "name": "strict", "description": "strict", "parameters": {"type": "object", "properties": {}}}}})
        monkeypatch.setattr(ollama_client, "chat", ChatRecorder(_call("strict", {"wrong": 1}), _say("ok")))
        out = make_manager().run("tool_caller", "go")
        assert out["tool_trace"][0]["output"].startswith("Error running tool")

    def test_endless_tool_calls_stop_at_the_iteration_cap(self, make_manager, monkeypatch, fake_tools):
        rec = ChatRecorder(_call("echo", {"x": "1"}))
        monkeypatch.setattr(ollama_client, "chat", rec)
        out = make_manager().run("tool_caller", "loop forever")
        assert len(rec.calls) == MAX_TOOL_ITERATIONS
        assert len(out["tool_trace"]) == MAX_TOOL_ITERATIONS and "توقفت" in out["content"]


class TestCouncilBackfill:
    TRACE = [
        {"tool": "ask_writer_agent", "args": {}, "output": "الشرح الكامل للمفهوم هنا وهو طويل جداً"},
        {"tool": "ask_coder_agent", "args": {}, "output": "def f():\n    return 1  # the code part"},
        {"tool": "web_search", "args": {}, "output": "unrelated tool output"},
    ]

    def test_dropped_delegate_reply_is_appended(self):
        out = AgentManager._backfill_dropped_delegates("## الكود\ndef f():\n    return 1  # the code part", self.TRACE)
        assert "رد وكيل الكاتب" in out and "الشرح الكامل" in out
        assert "رد وكيل المبرمج" not in out
        assert "unrelated" not in out

    def test_nothing_appended_when_everything_is_present(self):
        content = "الشرح الكامل للمفهوم هنا وهو طويل جداً\ndef f():\n    return 1  # the code part"
        assert AgentManager._backfill_dropped_delegates(content, self.TRACE) == content

    def test_council_applies_backfill_end_to_end(self, make_manager, monkeypatch):
        trace_call = _call("ask_writer_agent", {"task": "explain"})
        monkeypatch.setitem(registry._REGISTRY, "ask_writer_agent", {
            "fn": lambda task: "Deep explanation from the writer agent",
            "schema": registry._REGISTRY["ask_writer_agent"]["schema"],
        })
        monkeypatch.setattr(ollama_client, "chat", ChatRecorder(trace_call, _say("short summary only")))
        out = make_manager().run("council", "explain and code")
        assert "short summary only" in out["content"] and "Deep explanation from the writer agent" in out["content"]


class TestVision:
    @pytest.fixture
    def uploads(self, tmp_path, monkeypatch):
        monkeypatch.setattr(am, "UPLOADS_DIR", tmp_path)
        return tmp_path

    def test_sends_base64_image(self, make_manager, monkeypatch, uploads):
        (uploads / "p.jpg").write_bytes(b"\x89fake")
        rec = ChatRecorder(_say("a cat"))
        monkeypatch.setattr(ollama_client, "chat", rec)
        out = make_manager("std").run_vision("p.jpg", "what is it?")
        assert rec.calls[0]["messages"][-1]["images"] == [base64.b64encode(b"\x89fake").decode()]
        assert out["content"] == "a cat"

    def test_rejects_traversal_and_missing(self, make_manager, uploads):
        m = make_manager("std")
        with pytest.raises(ValueError):
            m.run_vision("../x.jpg", "q")
        with pytest.raises(ValueError):
            m.run_vision("nope.jpg", "q")

    def test_vision_is_unavailable_on_min_tier(self, make_manager):
        with pytest.raises(RuntimeError):
            make_manager("min").run_vision("p.jpg", "q")


class TestToolOutputCap:
    def test_long_tool_results_are_truncated_for_the_model_but_kept_in_the_trace(self, make_manager, monkeypatch):
        big = "x" * 20000
        monkeypatch.setitem(registry._REGISTRY, "big", {"fn": lambda **kw: big, "schema": {"type": "function", "function": {
            "name": "big", "description": "returns a lot", "parameters": {"type": "object", "properties": {}}}}})
        rec = ChatRecorder(_call("big", {}), _say("done"))
        monkeypatch.setattr(ollama_client, "chat", rec)
        out = make_manager().run("tool_caller", "go")
        sent = rec.calls[1]["messages"][-1]["content"]
        assert len(sent) < am.MAX_TOOL_OUTPUT_CHARS + 50 and sent.endswith("[output truncated]")
        assert out["tool_trace"][0]["output"] == big
