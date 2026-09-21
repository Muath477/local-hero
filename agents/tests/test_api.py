import sys

import pytest
from fastapi.testclient import TestClient

import orchestrator.agent_manager as am
from conftest import FakeDocumentStore


@pytest.fixture
def server(monkeypatch, no_manager, tmp_path):
    """api.server builds Router() and AgentManager() at import time — give it
    a fake DocumentStore so importing it never touches data/chroma or Ollama."""
    monkeypatch.setattr(am, "DocumentStore", lambda: FakeDocumentStore())
    sys.modules.pop("api.server", None)
    import api.server as srv

    monkeypatch.setattr(srv, "UPLOADS_DIR", tmp_path / "uploads")
    yield srv
    sys.modules.pop("api.server", None)


@pytest.fixture
def client(server):
    return TestClient(server.app)  # no `with`: startup (warm-up + MCP) must not run


def _fake_run(record):
    def run(role, message, history=None):
        record.update(role=role, message=message, history=history)
        return {"role": role, "content": "hello world reply", "tool_trace": [], "sources": ["a.txt"]}

    return run


def _body(model=None, **extra):
    return {"model": model, "messages": [{"role": "user", "content": "hi"}], **extra}


def test_health(client):
    r = client.get("/health").json()
    assert r["status"] == "ok" and r["hardware_tier"] in ("min", "std", "high")


def test_models_lists_auto_and_every_catalog_entry(client, server):
    ids = {m["id"] for m in client.get("/v1/models").json()["data"]}
    assert ids == set(server.MODEL_CATALOG) and "auto" in ids and "council" in ids


def test_explicit_model_skips_the_router(client, server, monkeypatch):
    seen = {}
    monkeypatch.setattr(server.manager, "run", _fake_run(seen))
    monkeypatch.setattr(server.router, "route", lambda m: pytest.fail("router must not run"))
    r = client.post("/v1/chat/completions", json=_body("coder")).json()
    assert seen["role"] == "coder"
    assert r["choices"][0]["message"] == {"role": "assistant", "content": "hello world reply"}
    assert r["x_router"]["method"] == "explicit" and r["x_sources"] == ["a.txt"]


def test_auto_and_unknown_models_use_the_router(client, server, monkeypatch):
    seen = {}
    monkeypatch.setattr(server.manager, "run", _fake_run(seen))
    monkeypatch.setattr(server.router, "route", lambda m: {"role": "writer_general", "method": "semantic", "confidence": 0.9})
    for model in ("auto", None, "some-random-client-model"):
        client.post("/v1/chat/completions", json=_body(model))
        assert seen["role"] == "writer_general"


def test_history_is_taken_from_the_request_and_system_messages_are_dropped(client, server, monkeypatch):
    seen = {}
    monkeypatch.setattr(server.manager, "run", _fake_run(seen))
    body = {"model": "coder", "messages": [
        {"role": "system", "content": "client system prompt"},
        {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]}
    client.post("/v1/chat/completions", json=body)
    assert seen["message"] == "q2"
    assert seen["history"] == [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]


def test_validation_errors(client):
    assert client.post("/v1/chat/completions", json={"model": "auto", "messages": []}).status_code == 400
    bad = {"model": "auto", "messages": [{"role": "assistant", "content": "x"}]}
    assert client.post("/v1/chat/completions", json=bad).status_code == 400


def test_disabled_role_becomes_503(client, server, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Role 'vision' is disabled")

    monkeypatch.setattr(server.manager, "run", boom)
    assert client.post("/v1/chat/completions", json=_body("coder")).status_code == 503


def test_streaming_reassembles_to_the_full_reply(client, server, monkeypatch):
    import json

    monkeypatch.setattr(server.manager, "run", _fake_run({}))
    r = client.post("/v1/chat/completions", json=_body("coder", stream=True))
    lines = [l[6:] for l in r.text.splitlines() if l.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    assert "".join(json.loads(l)["choices"][0]["delta"]["content"] for l in lines[:-1]) == "hello world reply"


class TestTarjumanEndpoint:
    def test_rejects_non_docx_with_localhero_error_shape(self, client):
        r = client.post("/tarjuman/translate", files={"file": ("a.pdf", b"x")}, data={"target_language": "Arabic"})
        assert r.status_code == 415 and "message" in r.json()["error"]

    def test_returns_translated_bytes(self, client, server, monkeypatch):
        monkeypatch.setattr(server, "translate_docx", lambda b, lang, **kw: b"TRANSLATED:" + lang.encode())
        r = client.post("/tarjuman/translate", files={"file": ("a.DOCX", b"x")}, data={"target_language": "Arabic"})
        assert r.status_code == 200 and r.content == b"TRANSLATED:Arabic"
        assert r.headers["content-type"] == server.DOCX_MEDIA_TYPE

    def test_translation_failure_is_502_with_error_shape(self, client, server, monkeypatch):
        def boom(b, lang, **kw):
            raise ValueError("not a docx")

        monkeypatch.setattr(server, "translate_docx", boom)
        r = client.post("/tarjuman/translate", files={"file": ("a.docx", b"x")}, data={"target_language": "Arabic"})
        assert r.status_code == 502 and "not a docx" in r.json()["error"]["message"]


    def test_glossary_is_forwarded_to_the_engine(self, client, server, monkeypatch):
        seen = {}
        monkeypatch.setattr(server, "translate_docx", lambda b, lang, **kw: seen.update(kw) or b"ok")
        r = client.post("/tarjuman/translate", files={"file": ("a.docx", b"x")},
                        data={"target_language": "Arabic", "glossary": '{"API": "واجهة برمجية"}'})
        assert r.status_code == 200 and seen["glossary"] == {"API": "واجهة برمجية"}

    @pytest.mark.parametrize("bad", ["not json", "[1, 2]", '"str"'])
    def test_invalid_glossary_is_a_400(self, client, bad):
        r = client.post("/tarjuman/translate", files={"file": ("a.docx", b"x")},
                        data={"target_language": "Arabic", "glossary": bad})
        assert r.status_code == 400 and "glossary" in r.json()["error"]["message"]


class TestUpload:
    def test_saves_and_indexes(self, client, server):
        r = client.post("/upload", files={"file": ("notes.txt", b"hello")})
        assert r.json() == {"filename": "notes.txt", "chunks_indexed": 3}
        assert (server.UPLOADS_DIR / "notes.txt").read_bytes() == b"hello"

    def test_ingest_error_is_400(self, client, server, monkeypatch):
        def boom(name):
            raise ValueError("Error: unreadable")

        monkeypatch.setattr(server.manager.documents, "ingest_file", boom)
        assert client.post("/upload", files={"file": ("x.bin", b"\x00")}).status_code == 400

    @pytest.mark.parametrize("evil", ["../evil.txt", "..\\evil.txt", "sub/../../evil.txt"])
    def test_filename_cannot_escape_the_uploads_directory(self, client, server, tmp_path, evil):
        client.post("/upload", files={"file": (evil, b"pwned")})
        assert not (tmp_path / "evil.txt").exists()
        assert not (server.UPLOADS_DIR.parent / "evil.txt").exists()


def test_root_serves_the_standalone_chat_ui(client):
    """agents/web/index.html is the single-file chat UI the agents service serves on `/`."""
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert 'dir="rtl"' in r.text and "fetch(" in r.text
