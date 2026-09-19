import pytest

from orchestrator import memory as mem
from orchestrator import ollama_client
from orchestrator.memory import CHUNK_OVERLAP, CHUNK_SIZE, ConversationMemory, DocumentStore, _chunk_text


class TestChunking:
    def test_empty_and_whitespace(self):
        assert _chunk_text("") == []
        assert _chunk_text("   \n\t ") == []

    def test_short_text_is_one_chunk(self):
        assert _chunk_text("hello") == ["hello"]

    def test_chunks_respect_size_and_overlap(self):
        text = "".join(chr(97 + (i // 10) % 26) + str(i % 10) for i in range(2000))  # distinguishable
        chunks = _chunk_text(text)
        assert len(chunks) > 2
        assert all(len(c) <= CHUNK_SIZE for c in chunks)
        # consecutive chunks share exactly CHUNK_OVERLAP characters of context
        for a, b in zip(chunks, chunks[1:]):
            assert a[-CHUNK_OVERLAP:] == b[:CHUNK_OVERLAP]

    def test_every_character_is_covered(self):
        text = "abcdefghij" * 300
        chunks = _chunk_text(text)
        rebuilt = chunks[0] + "".join(c[CHUNK_OVERLAP:] for c in chunks[1:])
        assert rebuilt == text

    def test_arabic_text_survives(self):
        text = "مرحبا بكم في المنصة. " * 100
        assert "مرحبا" in _chunk_text(text)[0]


class TestConversationMemory:
    @pytest.fixture
    def memory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mem, "CONVERSATIONS_DIR", tmp_path)
        return ConversationMemory(max_turns=2)

    def test_unknown_session_is_empty(self, memory):
        assert memory.get_history("nope") == []

    def test_history_is_trimmed_to_last_n_pairs(self, memory):
        for i in range(10):
            memory.add_turn("s", "user", f"q{i}")
            memory.add_turn("s", "assistant", f"a{i}")
        hist = memory.get_history("s")
        assert len(hist) == 4
        assert hist[0]["content"] == "q8" and hist[-1]["content"] == "a9"

    def test_arabic_round_trip_and_file_is_readable_utf8(self, memory, tmp_path):
        memory.add_turn("s", "user", "مرحبا")
        assert memory.get_history("s")[0]["content"] == "مرحبا"
        assert "مرحبا" in (tmp_path / "s.json").read_text(encoding="utf-8")  # ensure_ascii=False

    def test_sessions_are_isolated(self, memory):
        memory.add_turn("a", "user", "x")
        assert memory.get_history("b") == []


class TestDocumentStore:
    KEYWORDS = ["python", "cooking", "salary", "weather"]

    @staticmethod
    def _fake_embed(model, text):
        low = text.lower()
        return [low.count(k) + 0.01 for k in TestDocumentStore.KEYWORDS]

    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        import tools.file_reader as fr

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        monkeypatch.setattr(fr, "UPLOADS_DIR", uploads)
        monkeypatch.setattr(mem, "CHROMA_DIR", tmp_path / "chroma")
        monkeypatch.setattr(ollama_client, "embed", self._fake_embed)
        monkeypatch.setattr(ollama_client, "embed_batch", lambda model, texts: [self._fake_embed(model, t) for t in texts])
        monkeypatch.setattr(mem, "UPLOADS_DIR", uploads)
        s = DocumentStore()
        s._uploads = uploads
        return s

    def _put(self, store, name, text):
        (store._uploads / name).write_text(text, encoding="utf-8")
        return store.ingest_file(name)

    def test_empty_store_returns_nothing(self, store):
        assert store.query("anything") == []

    def test_ingest_then_retrieve_the_right_document(self, store):
        assert self._put(store, "code.txt", "python python python functions and classes") == 1
        assert self._put(store, "food.txt", "cooking cooking recipes for dinner") == 1
        hits = store.query("how do I write python code?", top_k=2)
        assert hits[0]["source"] == "code.txt"
        assert {h["source"] for h in hits} == {"code.txt", "food.txt"}

    def test_reingesting_replaces_instead_of_duplicating(self, store):
        self._put(store, "a.txt", "python one")
        self._put(store, "a.txt", "python two")
        hits = store.query("python", top_k=10)
        assert len(hits) == 1 and "two" in hits[0]["text"]

    def test_missing_file_raises_value_error(self, store):
        with pytest.raises(ValueError):
            store.ingest_file("ghost.txt")

    def test_empty_file_indexes_zero_chunks(self, store):
        assert self._put(store, "empty.txt", "   ") == 0


class TestEmbedderAwareCollections:
    def test_collection_name_is_derived_from_the_embedder(self):
        assert mem._collection_name("qwen3-embedding:0.6b") == "documents__qwen3_embedding_0_6b"
        assert mem._collection_name("nomic-embed-text:latest") != mem._collection_name("qwen3-embedding:0.6b")
        assert 3 <= len(mem._collection_name("hf.co/some-org/Some-Very-Long-Embedding-Model-Name-GGUF:Q8_0")) <= 63

    def test_switching_embedder_does_not_touch_the_old_collection(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mem, "CHROMA_DIR", tmp_path / "chroma")
        client = mem.chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        old = client.get_or_create_collection(mem._collection_name("nomic-embed-text:latest"))
        old.add(ids=["a"], documents=["x"], embeddings=[[0.1] * 768], metadatas=[{"source": "s.txt"}])
        monkeypatch.setattr(mem, "load_registry", lambda: {"embedder": {"ollama_tag": "qwen3-embedding:0.6b"}})
        store = DocumentStore()
        assert store.collection.name == "documents__qwen3_embedding_0_6b" != old.name
        assert store.query("anything") == []

    def test_reindex_missing_only_indexes_new_readable_files(self, tmp_path, monkeypatch):
        import tools.file_reader as fr

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        monkeypatch.setattr(fr, "UPLOADS_DIR", uploads)
        monkeypatch.setattr(mem, "UPLOADS_DIR", uploads)
        monkeypatch.setattr(mem, "CHROMA_DIR", tmp_path / "chroma")
        monkeypatch.setattr(ollama_client, "embed_batch", lambda model, texts: [[float(len(t)), 1.0] for t in texts])
        (uploads / "a.txt").write_text("alpha text", encoding="utf-8")
        (uploads / "b.txt").write_text("beta text", encoding="utf-8")
        (uploads / ".gitkeep").write_text("")
        (uploads / "pic.jpg").write_bytes(bytes([0xFF, 0xD8, 0xFF]) + bytes(range(128, 256)))
        store = DocumentStore()
        assert store.reindex_missing() == ["a.txt", "b.txt"]
        assert store.reindex_missing() == []  # nothing new the second time
        (uploads / "c.txt").write_text("gamma", encoding="utf-8")
        assert store.reindex_missing() == ["c.txt"]

    def test_ingest_embeds_in_batches_not_per_chunk(self, tmp_path, monkeypatch):
        import tools.file_reader as fr

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        monkeypatch.setattr(fr, "UPLOADS_DIR", uploads)
        monkeypatch.setattr(mem, "UPLOADS_DIR", uploads)
        monkeypatch.setattr(mem, "CHROMA_DIR", tmp_path / "chroma")
        sizes = []
        monkeypatch.setattr(ollama_client, "embed_batch", lambda model, texts: sizes.append(len(texts)) or [[1.0, 2.0]] * len(texts))
        (uploads / "big.txt").write_text("word " * 6000, encoding="utf-8")  # ~40 chunks
        n = DocumentStore().ingest_file("big.txt")
        assert n > mem.EMBED_BATCH and sizes == [mem.EMBED_BATCH, n - mem.EMBED_BATCH]
