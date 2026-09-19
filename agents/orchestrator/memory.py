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
import re
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
        if end >= len(text):
            break  # otherwise the next pass emits a tail that's fully inside this chunk
        start = end - CHUNK_OVERLAP
    return [c.strip() for c in chunks if c.strip()]


EMBED_BATCH = 32  # chunks per embedding request


def _collection_name(embedder_tag: str) -> str:
    # Vectors from different embedders have different sizes (nomic 768, Qwen3-Embedding 1024)
    # and live in different spaces — mixing them in one collection breaks every query. One
    # collection per embedder means switching models in models.yaml starts clean, and
    # DocumentStore.reindex_missing() rebuilds it from the files still in data/uploads/.
    return "documents__" + re.sub(r"[^A-Za-z0-9]+", "_", embedder_tag).strip("_")[:40]


class DocumentStore:
    def __init__(self):
        self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.embedder_tag = load_registry()["embedder"]["ollama_tag"]
        self.collection = self.client.get_or_create_collection(_collection_name(self.embedder_tag))

    def reindex_missing(self) -> list[str]:
        """Indexes every uploaded file the current collection doesn't know yet
        (after an embedder change, or files dropped into data/uploads by hand).
        Unreadable files are skipped. Returns the filenames indexed."""
        known = {m["source"] for m in self.collection.get(include=["metadatas"])["metadatas"]}
        indexed = []
        for path in sorted(UPLOADS_DIR.glob("*")) if UPLOADS_DIR.exists() else []:
            if not path.is_file() or path.name.startswith(".") or path.name in known:
                continue
            try:
                if self.ingest_file(path.name):
                    indexed.append(path.name)
            except ValueError:
                pass  # not text we can read (an image, an archive, ...)
        return indexed

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

        embeddings = []
        for i in range(0, len(chunks), EMBED_BATCH):  # one request per batch, not per chunk
            embeddings += ollama.embed_batch(self.embedder_tag, chunks[i:i + EMBED_BATCH])
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
