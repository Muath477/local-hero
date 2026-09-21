import pytest

from orchestrator import ollama_client
from orchestrator import router as router_mod
from orchestrator.router import ROLE_EXAMPLES, Router, _cosine


def test_cosine_basics():
    assert _cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert _cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert _cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert _cosine([0, 0], [1, 1]) == 0.0  # zero vector must not divide by zero


@pytest.fixture
def router(monkeypatch):
    r = Router()
    # Hand-built 3-d "embeddings": one axis per role, so scores are predictable.
    r._role_vectors = {
        "coder": [[1.0, 0.0, 0.0]],
        "writer_general": [[0.0, 1.0, 0.0]],
        "tool_caller": [[0.0, 0.0, 1.0]],
    }
    return r


def _embed_as(monkeypatch, vec):
    monkeypatch.setattr(ollama_client, "embed", lambda model, text: vec)


def _chat_returns(monkeypatch, content, calls=None):
    def fake(model, messages, **kw):
        if calls is not None:
            calls.append((model, messages, kw))
        return {"message": {"content": content}}

    monkeypatch.setattr(ollama_client, "chat", fake)


def test_confident_match_uses_semantic_stage_only(router, monkeypatch):
    _embed_as(monkeypatch, [1.0, 0.05, 0.0])
    calls = []
    _chat_returns(monkeypatch, '{"role": "writer_general"}', calls)
    decision = router.route("write me a python function")
    assert decision["role"] == "coder" and decision["method"] == "semantic"
    assert calls == [], "stage 2 (LLM) must not fire when stage 1 is confident"


def test_ambiguous_scores_fall_back_to_llm(router, monkeypatch):
    _embed_as(monkeypatch, [1.0, 1.0, 0.0])  # coder and writer tie -> margin 0
    _chat_returns(monkeypatch, '{"role": "tool_caller"}')
    decision = router.route("something vague")
    assert decision == {"role": "tool_caller", "method": "llm_fallback", "confidence": None}


def test_low_confidence_falls_back_to_llm(router, monkeypatch):
    _embed_as(monkeypatch, [1.0, 1.0, 1.0])
    _chat_returns(monkeypatch, '{"role": "coder"}')
    assert router.route("x")["method"] == "llm_fallback"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"role": "coder"}', "coder"),
        ('{"role": "not_a_role"}', "writer_general"),
        ("coder", "writer_general"),
        ('["coder"]', "writer_general"),
        ("null", "writer_general"),
        ("", "writer_general"),
        # Small models very often wrap JSON in a fence or add prose around it.
        ('```json\n{"role": "coder"}\n```', "coder"),
        ('Sure! {"role": "tool_caller"} hope that helps', "tool_caller"),
        ('{"role": "coder"}\n', "coder"),
    ],
)
def test_llm_route_parsing(router, monkeypatch, raw, expected):
    _chat_returns(monkeypatch, raw)
    assert router._llm_route("anything") == expected


def test_llm_route_is_deterministic(router, monkeypatch):
    calls = []
    _chat_returns(monkeypatch, '{"role": "coder"}', calls)
    router._llm_route("x")
    assert calls[0][2]["temperature"] == 0.0


def test_warm_up_partitions_vectors_per_role(monkeypatch):
    r = Router()
    total = sum(len(v) for v in ROLE_EXAMPLES.values())
    monkeypatch.setattr(
        ollama_client, "embed_batch", lambda model, texts: [[float(i)] for i in range(len(texts))]
    )
    r.warm_up()
    assert set(r._role_vectors) == set(ROLE_EXAMPLES)
    assert sum(len(v) for v in r._role_vectors.values()) == total
    for role, vecs in r._role_vectors.items():
        assert len(vecs) == len(ROLE_EXAMPLES[role])


def test_thresholds_are_sane():
    assert 0.3 < router_mod.CONFIDENCE_THRESHOLD < 0.95
    assert 0 < router_mod.MARGIN_THRESHOLD < 0.3


class TestVisionAndStructuredOutput:
    def test_vision_is_never_a_text_routing_target(self, monkeypatch):
        r = Router()
        r._role_vectors = {"vision": [[1.0, 0.0]], "writer_general": [[0.0, 1.0]]}
        monkeypatch.setattr(ollama_client, "embed", lambda model, text: [1.0, 0.0])  # closest to vision
        monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: {"message": {"content": '{"role": "vision"}'}})
        decision = r.route("what is in this image")
        assert decision["role"] == "writer_general"

    def test_vision_can_be_allowed_explicitly(self, monkeypatch):
        r = Router()
        r._role_vectors = {"vision": [[1.0, 0.0]], "writer_general": [[0.0, 1.0]]}
        monkeypatch.setattr(ollama_client, "embed", lambda model, text: [1.0, 0.02])
        assert r.route("x", exclude=frozenset())["role"] == "vision"

    def test_llm_stage_is_constrained_to_the_routable_roles(self, router, monkeypatch):
        calls = []
        _chat_returns(monkeypatch, '{"role": "coder"}', calls)
        router._llm_route("anything")
        kw = calls[0][2]
        enum = kw["format"]["properties"]["role"]["enum"]
        assert "vision" not in enum and "coder" in enum and kw["format"]["required"] == ["role"]
        assert "vision" not in calls[0][1][0]["content"]

    def test_a_role_outside_the_allowed_set_is_rejected(self):
        assert router_mod.parse_role('{"role": "vision"}', ["coder"]) is None
        assert router_mod.parse_role('{"role": "coder"}', ["coder"]) == "coder"
