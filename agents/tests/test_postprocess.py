import pytest

from benchmarks.tasks import runs_against
from orchestrator import ollama_client
from orchestrator.postprocess import fix_code_punctuation


@pytest.mark.parametrize("bad, good", [
    ("f(a\u060c b)", "f(a, b)"), ("x = 2 \u00d7 3", "x = 2 * 3"), ("y = 8 \u00f7 2", "y = 8 / 2"),
    ("s = \u201chi\u201d", 's = "hi"'), ("a\u00a0=\u00a01", "a = 1"), ("if x\u061f", "if x?"),
])
def test_punctuation_inside_a_code_block_is_fixed(bad, good):
    assert fix_code_punctuation(f"```python\n{bad}\n```") == f"```python\n{good}\n```"


def test_prose_outside_the_fence_is_untouched():
    text = "\u0647\u0630\u0627\u060c \u0634\u0631\u062d \u00d7\n```python\nf(a\u060c b)\n```\n\u0628\u0639\u062f\u060c \u0646\u0647\u0627\u064a\u0629"
    out = fix_code_punctuation(text)
    assert out.startswith("\u0647\u0630\u0627\u060c \u0634\u0631\u062d \u00d7") and out.endswith("\u0628\u0639\u062f\u060c \u0646\u0647\u0627\u064a\u0629")
    assert "f(a, b)" in out


def test_several_blocks_and_an_unclosed_final_block():
    out = fix_code_punctuation("```py\na\u060cb\n```\ntext \u060c\n```python\nc\u060cd")
    assert "a,b" in out and "text \u060c" in out and out.endswith("c,d")


def test_text_without_code_is_returned_unchanged():
    assert fix_code_punctuation("no code \u060c here") == "no code \u060c here"


def test_the_coder_role_gets_clean_code(make_manager, monkeypatch):
    monkeypatch.setattr(ollama_client, "chat", lambda model, messages, **kw: {"message": {"content": "```python\nreturn a\u060c b\n```"}})
    out = make_manager().run("coder", "x")
    assert "return a, b" in out["content"]


def test_other_roles_are_not_rewritten(make_manager, monkeypatch):
    raw = "```python\nreturn a\u060c b\n```"
    monkeypatch.setattr(ollama_client, "chat", lambda model, messages, **kw: {"message": {"content": raw}})
    assert make_manager().run("writer_general", "x")["content"] == raw


def test_benchmark_scores_the_same_way_production_would():
    reply = {"content": "```python\ndef pair(a\u060c b):\n    return a \u00d7 b\n```", "tool_calls": []}
    assert runs_against("assert pair(2, 3) == 6")(reply) == (True, "ok")


# ── foreign-script repair ───────────────────────────────────────────────────

from orchestrator.postprocess import leak_count, needs_script_repair

AR_Q = "\u0648\u0634 \u0627\u0644\u0645\u0644\u0641\u0627\u062a \u0627\u0644\u0644\u064a \u0631\u0641\u0639\u062a\u0647\u0627\u061f"
LEAK = "\u062d\u062c\u0645\u0647 56 \u0431\u0430\u0439\u0442"          # Arabic + Cyrillic "bайт"


@pytest.mark.parametrize("text, n", [
    ("clean \u0639\u0631\u0628\u064a", 0), (LEAK, 4), ("\u9884\u7b97 x", 2), ("\u05e9\u05dc\u05d5\u05dd", 4),
    ("```python\nprint('\u0431')\n```", 0), ("", 0),
])
def test_leak_count_ignores_code_blocks(text, n):
    assert leak_count(text) == n


class TestNeedsRepair:
    def test_arabic_question_with_a_leak(self):
        assert needs_script_repair(AR_Q, LEAK)

    def test_clean_answers_are_left_alone(self):
        assert not needs_script_repair(AR_Q, "\u062d\u062c\u0645\u0647 56 \u0628\u0627\u064a\u062a")

    @pytest.mark.parametrize("q", ["\u062a\u0631\u062c\u0645 \u0647\u0630\u0627 \u0644\u0644\u0631\u0648\u0633\u064a\u0629", "\u0627\u0643\u062a\u0628 \u0644\u064a \u0628\u0627\u0644\u0635\u064a\u0646\u064a", "say it in Russian"])
    def test_user_asked_for_that_script(self, q):
        assert not needs_script_repair(q, LEAK)

    def test_english_question_is_never_repaired(self):
        assert not needs_script_repair("what files did I upload?", LEAK)

    def test_translation_tool_output_is_exempt(self):
        assert not needs_script_repair(AR_Q, LEAK, [{"tool": "translate_text"}])
        assert needs_script_repair(AR_Q, LEAK, [{"tool": "web_search"}])


class TestRepairWiring:
    def _chat(self, monkeypatch, first, second):
        calls = []

        def chat(model, messages, **kw):
            calls.append(messages[0]["content"])
            return {"message": {"content": first if len(calls) == 1 else second}}

        monkeypatch.setattr(ollama_client, "chat", chat)
        return calls

    def test_a_leak_triggers_one_rewrite_and_it_is_kept_when_cleaner(self, make_manager, monkeypatch):
        calls = self._chat(monkeypatch, LEAK, "\u062d\u062c\u0645\u0647 56 \u0628\u0627\u064a\u062a")
        out = make_manager().run("writer_general", AR_Q)
        assert len(calls) == 2 and "\u0623\u0639\u062f \u0643\u062a\u0627\u0628\u0629" in calls[1]
        assert out["content"] == "\u062d\u062c\u0645\u0647 56 \u0628\u0627\u064a\u062a" and out["script_repaired"] is True

    def test_a_rewrite_that_is_not_cleaner_is_discarded(self, make_manager, monkeypatch):
        self._chat(monkeypatch, LEAK, LEAK + " \u0431")
        out = make_manager().run("writer_general", AR_Q)
        assert out["content"] == LEAK and "script_repaired" not in out

    def test_clean_answers_cost_no_extra_call(self, make_manager, monkeypatch):
        calls = self._chat(monkeypatch, "\u0646\u0635 \u0633\u0644\u064a\u0645", "x")
        make_manager().run("writer_general", AR_Q)
        assert len(calls) == 1

    def test_code_answers_with_foreign_letters_in_the_code_are_not_rewritten(self, make_manager, monkeypatch):
        calls = self._chat(monkeypatch, "```python\nx = '\u0431'\n```", "x")
        make_manager().run("coder", AR_Q)
        assert len(calls) == 1
