import pytest
import requests

from orchestrator import ollama_client


class Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


@pytest.fixture
def posted(monkeypatch):
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append({"url": url, "json": json, "timeout": timeout})
        return Resp({"message": {"content": "ok"}, "embeddings": [[1.0]], "embedding": [1.0]})

    monkeypatch.setattr(ollama_client.requests, "post", fake_post)
    return sent


def test_chat_payload_defaults(posted):
    ollama_client.chat("m", [{"role": "user", "content": "x"}])
    body = posted[0]["json"]
    assert posted[0]["url"].endswith("/api/chat")
    assert body["stream"] is False and body["keep_alive"] == "5m"
    assert body["options"] == {"temperature": 0.3}
    assert "tools" not in body and "think" not in body  # models without a thinking mode reject the flag


@pytest.mark.parametrize("flag", [True, False])
def test_think_flag_is_forwarded_only_when_set(posted, flag):
    ollama_client.chat("m", [], think=flag)
    assert posted[0]["json"]["think"] is flag


def test_tools_are_forwarded(posted):
    schema = [{"type": "function", "function": {"name": "t"}}]
    ollama_client.chat("m", [], tools=schema)
    assert posted[0]["json"]["tools"] == schema


def test_embed_batch_uses_the_batched_endpoint(posted):
    assert ollama_client.embed_batch("e", ["a", "b"]) == [[1.0]]
    assert posted[0]["url"].endswith("/api/embed") and posted[0]["json"]["input"] == ["a", "b"]


def test_is_alive_is_false_when_ollama_is_down(monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError()

    monkeypatch.setattr(ollama_client.requests, "get", down)
    assert ollama_client.is_alive() is False


def test_agent_manager_passes_the_roles_think_setting(make_manager, monkeypatch):
    seen = []
    monkeypatch.setattr(ollama_client, "chat", lambda model, messages, **kw: seen.append(kw) or {"message": {"content": "x"}})
    m = make_manager()
    m.registry["writer_general"] = {**m.registry["writer_general"], "think": False}
    m.run("writer_general", "hi")
    m.run("coder", "hi")  # no `think` key configured -> None -> flag not sent
    assert seen[0]["think"] is False and seen[1]["think"] is None
