"""Two different kinds of state, backed by two different stores because
they have different access patterns:

  ConversationMemory — ordered per-session turn history. Plain JSON files,
  one per session. No semantic search needed here: a chat turn is always
  read back "last N in order", so a vector DB would be the wrong tool.

  DocumentStore — chunks + embeddings for uploaded files, queried by
  semantic similarity. Backed by ChromaDB. This is the one place a vector
  store actually earns its keep.

Both use Ollama's nomic-embed-text for embeddings (same model the router
already loads) instead of Chroma's default embedding function, so nothing
extra gets pulled in just for this.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")  # avoid a network call/hang on first init

import chromadb

from . import ollama_client as ollama
from .hardware import load_registry

CONVERSATIONS_DIR = Path(__file__).parent.parent / "data" / "conversations"
CHROMA_DIR = Path(__file__).parent.parent / "data" / "chroma"
UPLOADS_DIR = Path(__file__).parent.parent / "data" / "uploads"

CHUNK_SIZE = 800       # characters per chunk
CHUNK_OVERLAP = 150    # characters shared between consecutive chunks


class ConversationMemory:
    def __init__(self, max_turns: int = 12):
        self.max_turns = max_turns  # user+assistant pairs kept per session
        CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return CONVERSATIONS_DIR / f"{session_id}.json"

    def add_turn(self, session_id: str, role: str, content: str):
        turns = self._load(session_id)
        turns.append({"role": role, "content": content})
        self._path(session_id).write_text(
            json.dumps(turns, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_history(self, session_id: str) -> list[dict]:
        turns = self._load(session_id)
        return turns[-(self.max_turns * 2):]

    def _load(self, session_id: str) -> list[dict]:
        path = self._path(session_id)
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))


def _chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end])
        start = end - CHUNK_OVERLAP
    return [c.strip() for c in chunks if c.strip()]


class DocumentStore:
    def __init__(self):
        self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.collection = self.client.get_or_create_collection("documents")
        self.embedder_tag = load_registry()["embedder"]["ollama_tag"]

    def ingest_file(self, filename: str) -> int:
        """Reads a file already sitting in data/uploads/, chunks it, embeds
        each chunk, and stores it. Returns the number of chunks stored.
        """
        from tools.file_reader import read_uploaded_file  # local import avoids a cycle at module load

        text = read_uploaded_file(filename)
        if text.startswith("Error:"):
            raise ValueError(text)

        chunks = _chunk_text(text)
        if not chunks:
            return 0

        # Re-ingesting the same filename replaces its old chunks rather than
        # duplicating them.
        self.collection.delete(where={"source": filename})

        embeddings = [ollama.embed(self.embedder_tag, chunk) for chunk in chunks]
        ids = [f"{filename}::{i}::{uuid.uuid4().hex[:8]}" for i in range(len(chunks))]
        metadatas = [{"source": filename, "chunk_index": i} for i in range(len(chunks))]

        self.collection.add(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
        return len(chunks)

    def query(self, text: str, top_k: int = 4) -> list[dict]:
        if self.collection.count() == 0:
            return []
        query_embedding = ollama.embed(self.embedder_tag, text)
        result = self.collection.query(query_embeddings=[query_embedding], n_results=top_k)
        hits = []
        for doc, meta, dist in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            hits.append({"text": doc, "source": meta["source"], "distance": dist})
        return hits
