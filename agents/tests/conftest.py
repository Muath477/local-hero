import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ollama_tags() -> set[str] | None:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        r.raise_for_status()
        return {m["name"] for m in r.json().get("models", [])}
    except requests.RequestException:
        return None


@pytest.fixture(scope="session")
def ollama_tags() -> set[str]:
    tags = _ollama_tags()
    if tags is None:
        pytest.skip("Ollama isn't running on localhost:11434")
    return tags


@pytest.fixture
def no_manager(monkeypatch):
    """AgentManager.__init__ registers itself as a module-global in
    tools.agent_tools; monkeypatching that global first makes pytest restore
    it afterwards so tests can't leak a manager into each other."""
    from tools import agent_tools

    monkeypatch.setattr(agent_tools, "_manager", None)
    return agent_tools


class FakeDocumentStore:
    def __init__(self, hits=None):
        self.hits = hits or []
        self.queries = []

    def ingest_file(self, filename):
        return 3

    def query(self, text, top_k=4):
        self.queries.append(text)
        return self.hits


@pytest.fixture
def make_manager(monkeypatch, no_manager):
    """Builds an AgentManager on a chosen hardware tier with no ChromaDB and no Ollama."""
    import orchestrator.agent_manager as am

    def _make(tier="std", hits=None):
        monkeypatch.setattr(am, "DocumentStore", lambda: FakeDocumentStore(hits))
        monkeypatch.setattr(am, "resolve_tier", lambda: tier)
        return am.AgentManager()

    return _make
