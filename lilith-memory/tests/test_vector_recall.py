"""Tests for :mod:`lilith_memory.vector_recall`.

Covers:
    - HashEmbedder: determinism, dim validation, n-gram range, normalization,
      slot/sign hashing trick, similarity, empty input, batch API
    - VectorRecall: add/get/search/delete/clear/stats, idempotency, source
      filter, min_score, in-memory + file-based, dim mismatch handling,
      add_document alias, add_text with strategy override
    - RecallHit: to_dict serialization
    - chunk_and_recall: end-to-end one-shot helper
"""

from __future__ import annotations

import math
import os
import sqlite3
import tempfile

import pytest

from lilith_memory.chunker import Chunk, ChunkStrategy, SemanticChunker
from lilith_memory.vector_recall import (
    HashEmbedder,
    RecallHit,
    VectorRecall,
    chunk_and_recall,
)


# ── HashEmbedder ────────────────────────────────────────────────────────


class TestHashEmbedderBasics:
    """Core HashEmbedder behavior."""

    def test_default_dim(self):
        e = HashEmbedder()
        assert e.dim == 1024

    def test_default_ngrams(self):
        e = HashEmbedder()
        assert e.ngram_min == 1
        assert e.ngram_max == 1

    def test_valid_dim(self):
        e = HashEmbedder(dim=256)
        assert e.dim == 256

    def test_invalid_dim_zero(self):
        with pytest.raises(ValueError):
            HashEmbedder(dim=0)

    def test_invalid_dim_negative(self):
        with pytest.raises(ValueError):
            HashEmbedder(dim=-1)

    def test_invalid_ngram_range(self):
        with pytest.raises(ValueError):
            HashEmbedder(ngram_min=2, ngram_max=1)

    def test_invalid_ngram_min_zero(self):
        with pytest.raises(ValueError):
            HashEmbedder(ngram_min=0)


class TestHashEmbedderOutput:
    """The embed() output shape and content."""

    def test_output_length_matches_dim(self):
        e = HashEmbedder(dim=128)
        v = e.embed("hello world")
        assert isinstance(v, list)
        assert len(v) == 128

    def test_empty_input_returns_zeros(self):
        e = HashEmbedder(dim=64)
        v = e.embed("")
        assert v == [0.0] * 64

    def test_whitespace_only_returns_zeros(self):
        e = HashEmbedder(dim=64)
        v = e.embed("    \n\t   ")
        assert v == [0.0] * 64

    def test_normalized_vectors_have_unit_norm(self):
        e = HashEmbedder(dim=256, normalize=True)
        v = e.embed("the quick brown fox jumps over the lazy dog")
        norm = math.sqrt(sum(x * x for x in v))
        assert abs(norm - 1.0) < 1e-9

    def test_unnormalized_vectors_keep_weight(self):
        e = HashEmbedder(dim=256, normalize=False)
        v = e.embed("hello hello hello world")
        norm = math.sqrt(sum(x * x for x in v))
        assert norm > 1.0  # not unit length

    def test_deterministic_output(self):
        e = HashEmbedder(dim=128)
        v1 = e.embed("deterministic is good")
        v2 = e.embed("deterministic is good")
        assert v1 == v2

    def test_different_text_different_vector(self):
        e = HashEmbedder(dim=128)
        v1 = e.embed("hello world")
        v2 = e.embed("goodbye world")
        assert v1 != v2

    def test_batch_preserves_order(self):
        e = HashEmbedder(dim=64)
        texts = ["alpha", "beta", "gamma"]
        vecs = e.embed_batch(texts)
        assert len(vecs) == 3
        for v, t in zip(vecs, texts):
            assert len(v) == 64
            assert v == e.embed(t)

    def test_batch_empty(self):
        e = HashEmbedder(dim=64)
        assert e.embed_batch([]) == []


class TestHashEmbedderNgrams:
    """N-gram range behavior."""

    def test_unigrams_only(self):
        e = HashEmbedder(dim=256, ngram_min=1, ngram_max=1)
        v = e.embed("hello world")
        assert any(x != 0.0 for x in v)

    def test_unigrams_and_bigrams(self):
        e = HashEmbedder(dim=256, ngram_min=1, ngram_max=2)
        v = e.embed("hello world hello")
        assert any(x != 0.0 for x in v)

    def test_bigrams_only(self):
        e = HashEmbedder(dim=256, ngram_min=2, ngram_max=2)
        v = e.embed("hello world")
        assert any(x != 0.0 for x in v)

    def test_trigrams_capture_phrases(self):
        e1 = HashEmbedder(dim=1024, ngram_min=1, ngram_max=1)
        e3 = HashEmbedder(dim=1024, ngram_min=3, ngram_max=3)
        # Trigrams care about word order; unigrams don't
        a = "dog bites man"
        b = "man bites dog"
        s_uni = e1.similarity(e1.embed(a), e1.embed(b))
        s_tri = e3.similarity(e3.embed(a), e3.embed(b))
        # Trigrams should distinguish order; the assertion is that they differ
        # (not that one is greater than the other — exact values depend on hash)
        assert s_uni != s_tri


class TestHashEmbedderHashingTrick:
    """The slot/sign hashing trick internals."""

    def test_slot_within_dim(self):
        e = HashEmbedder(dim=128)
        for token in ["hello", "world", "yggdrasil", "odin", "python", "alpha"]:
            s = e.token_slot(token)
            assert 0 <= s < 128

    def test_sign_is_plus_or_minus_one(self):
        e = HashEmbedder(dim=64)
        for token in ["hello", "world", "yggdrasil", "alpha", "beta"]:
            s = e.token_sign(token)
            assert s in (-1, 1)

    def test_different_tokens_can_share_slot(self):
        # It's OK if two tokens hash to the same slot; the sign differentiates
        e = HashEmbedder(dim=4)  # very small to force collisions
        slots = {e.token_slot(t) for t in ["a", "b", "c", "d", "e", "f", "g"]}
        # With dim=4 and 7 tokens, collisions are certain
        assert len(slots) <= 4


class TestHashEmbedderSimilarity:
    """The similarity() method."""

    def test_identical_vectors_have_score_one(self):
        e = HashEmbedder(dim=64)
        v = e.embed("hello world")
        assert e.similarity(v, v) == pytest.approx(1.0, abs=1e-9)

    def test_zero_vector_returns_zero(self):
        e = HashEmbedder(dim=64)
        v = e.embed("hello")
        z = [0.0] * 64
        assert e.similarity(v, z) == 0.0
        assert e.similarity(z, v) == 0.0
        assert e.similarity(z, z) == 0.0

    def test_similar_texts_score_higher_than_unrelated(self):
        e = HashEmbedder(dim=512, ngram_min=1, ngram_max=2)
        # Topic A: Norse mythology
        a = e.embed("Yggdrasil is the world tree connecting nine realms")
        b = e.embed("Odin is the Allfather, ruler of Asgard")
        # Topic B: Cooking
        c = e.embed("Boil water, add salt, then cook pasta al dente")
        sim_ab = e.similarity(a, b)
        sim_ac = e.similarity(a, c)
        assert sim_ab > sim_ac

    def test_symmetry(self):
        e = HashEmbedder(dim=64)
        v1 = e.embed("alpha beta gamma")
        v2 = e.embed("delta epsilon zeta")
        assert e.similarity(v1, v2) == pytest.approx(e.similarity(v2, v1), abs=1e-12)


# ── VectorRecall ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db():
    """File-based DB in a temp dir, cleaned up after the test."""
    td = tempfile.mkdtemp()
    p = os.path.join(td, "recall.db")
    yield p
    # Best-effort cleanup
    for f in os.listdir(td):
        try:
            os.remove(os.path.join(td, f))
        except OSError:
            pass
    try:
        os.rmdir(td)
    except OSError:
        pass


@pytest.fixture
def mem_recall():
    """In-memory VectorRecall with a small dim for fast tests."""
    e = HashEmbedder(dim=64)
    return VectorRecall(":memory:", embedder=e)


@pytest.fixture
def make_chunk():
    """Helper to build a Chunk with sensible defaults."""

    def _make(text: str, idx: int = 0, source: str = "test", **meta) -> Chunk:
        meta.setdefault("source_id", source)
        meta.setdefault("chunk_index", idx)
        meta.setdefault("start_offset", 0)
        meta.setdefault("end_offset", len(text))
        meta.setdefault("strategy", "test")
        return Chunk(
            id=f"chunk_{idx:04d}_{abs(hash(text)) % 10**8}",
            text=text,
            index=idx,
            start_offset=0,
            end_offset=len(text),
            strategy="test",
            metadata=meta,
            token_estimate=max(1, len(text) // 4),
        )

    return _make


class TestVectorRecallBasics:
    """Constructor + persistence basics."""

    def test_in_memory_db(self):
        e = HashEmbedder(dim=64)
        r = VectorRecall(":memory:", embedder=e)
        assert r.count() == 0
        assert r.stats()["vectors"] == 0

    def test_file_based_db_creates_file(self, tmp_db):
        e = HashEmbedder(dim=64)
        r = VectorRecall(tmp_db, embedder=e)
        assert os.path.exists(tmp_db)

    def test_db_path_creates_parents(self, tmp_db):
        nested = os.path.join(os.path.dirname(tmp_db), "sub", "deep", "x.db")
        e = HashEmbedder(dim=64)
        try:
            r = VectorRecall(nested, embedder=e)
            assert os.path.exists(nested)
        finally:
            try:
                os.remove(nested)
                os.removedirs(os.path.dirname(nested))
            except OSError:
                pass

    def test_default_embedder_and_chunker(self, tmp_db):
        r = VectorRecall(tmp_db)
        assert isinstance(r.embedder, HashEmbedder)
        assert isinstance(r.chunker, SemanticChunker)
        assert r.embedder.dim == 1024

    def test_custom_embedder_dim_propagates_to_stats(self, tmp_db):
        r = VectorRecall(tmp_db, embedder=HashEmbedder(dim=256))
        assert r.stats()["embedder_dim"] == 256

    def test_reopen_preserves_data(self, tmp_db):
        e = HashEmbedder(dim=64)
        r1 = VectorRecall(tmp_db, embedder=e)
        r1.add_text("the quick brown fox", source_id="a")
        # Reopen
        r2 = VectorRecall(tmp_db, embedder=e)
        assert r2.count() == 1


class TestVectorRecallAdd:
    """Write API: add, add_text, add_document."""

    def test_add_single_chunk(self, mem_recall, make_chunk):
        c = make_chunk("hello world")
        vid = mem_recall.add(c, source_id="d1")
        assert isinstance(vid, int) and vid > 0
        assert mem_recall.count() == 1

    def test_add_idempotent_by_chunk_id(self, mem_recall, make_chunk):
        c = make_chunk("hello world")
        v1 = mem_recall.add(c, source_id="d1")
        v2 = mem_recall.add(c, source_id="d1")
        assert v1 == v2
        assert mem_recall.count() == 1

    def test_add_metadata_merges(self, mem_recall):
        # No pre-existing metadata in the chunk
        c = Chunk(
            id="c_meta_1",
            text="hello",
            index=0,
            start_offset=0,
            end_offset=5,
            strategy="test",
            metadata={},
            token_estimate=1,
        )
        mem_recall.add(c, source_id="d1", metadata={"tag": "x", "priority": 5})
        hit = mem_recall.get(c.id)
        assert hit is not None
        assert hit.chunk.metadata["tag"] == "x"
        assert hit.chunk.metadata["priority"] == 5

    def test_add_text_chunks_and_stores(self, mem_recall):
        vids = mem_recall.add_text(
            "First sentence. Second sentence. Third sentence. Fourth sentence.",
            source_id="d1",
        )
        # Default chunker probably makes multiple chunks
        assert len(vids) >= 1
        assert mem_recall.count() == len(vids)

    def test_add_text_with_strategy(self, mem_recall):
        # Code strategy on Python source
        code = (
            "def foo():\n    pass\n\n"
            "def bar():\n    return 42\n\n"
            "class Baz:\n    def method(self):\n        return 1\n"
        )
        vids = mem_recall.add_text(code, source_id="py", strategy="code")
        assert len(vids) >= 1

    def test_add_document_is_alias_for_add_text(self, mem_recall):
        v1 = mem_recall.add_text("hello", source_id="d")
        v2 = mem_recall.add_document("d", "hello")
        # Both produce one chunk each
        assert len(v1) == len(v2)

    def test_add_text_empty_input(self, mem_recall):
        vids = mem_recall.add_text("", source_id="d")
        # No chunks created from empty text
        assert vids == []
        assert mem_recall.count() == 0


class TestVectorRecallDelete:
    """Delete operations."""

    def test_delete_source(self, mem_recall):
        mem_recall.add_text("hello world", source_id="a")
        mem_recall.add_text("another doc", source_id="b")
        assert mem_recall.count() == 2
        n = mem_recall.delete_source("a")
        assert n == 1
        assert mem_recall.count() == 1
        # b is still there
        assert mem_recall.list_sources()[0]["source_id"] == "b"

    def test_delete_source_missing(self, mem_recall):
        assert mem_recall.delete_source("nope") == 0

    def test_delete_chunk(self, mem_recall, make_chunk):
        c1 = make_chunk("alpha", idx=0)
        c2 = make_chunk("beta", idx=1)
        mem_recall.add(c1, source_id="d")
        mem_recall.add(c2, source_id="d")
        assert mem_recall.delete_chunk(c1.id) is True
        assert mem_recall.delete_chunk(c1.id) is False  # already gone
        assert mem_recall.count() == 1

    def test_delete_chunk_missing(self, mem_recall):
        assert mem_recall.delete_chunk("does-not-exist") is False

    def test_clear(self, mem_recall):
        mem_recall.add_text("one two three", source_id="a")
        mem_recall.add_text("four five six", source_id="b")
        mem_recall.clear()
        assert mem_recall.count() == 0
        assert mem_recall.list_sources() == []


class TestVectorRecallSearch:
    """The main search() method."""

    def test_search_empty_store(self, mem_recall):
        hits = mem_recall.search("anything", top_k=5)
        assert hits == []

    def test_search_finds_relevant(self, mem_recall):
        mem_recall.add_text(
            "Yggdrasil is the world tree connecting nine realms of Norse cosmology.",
            source_id="myth",
        )
        mem_recall.add_text(
            "Python is a high-level programming language used for AI and web dev.",
            source_id="tech",
        )
        mem_recall.add_text(
            "Spaghetti carbonara is a classic Italian pasta dish with eggs and bacon.",
            source_id="food",
        )
        hits = mem_recall.search("Tell me about Yggdrasil", top_k=3)
        assert len(hits) > 0
        assert hits[0].source_id == "myth"

    def test_search_top_k_respected(self, mem_recall):
        for i in range(10):
            mem_recall.add_text(f"document number {i} about topic {i}", source_id=f"d{i}")
        hits = mem_recall.search("document", top_k=3)
        assert len(hits) == 3

    def test_search_invalid_top_k(self, mem_recall):
        with pytest.raises(ValueError):
            mem_recall.search("hi", top_k=0)
        with pytest.raises(ValueError):
            mem_recall.search("hi", top_k=-1)

    def test_search_source_filter(self, mem_recall):
        mem_recall.add_text("Yggdrasil is a tree", source_id="myth")
        mem_recall.add_text("Python is a snake and a language", source_id="tech")
        # Without filter: both
        hits = mem_recall.search("snake or tree", top_k=5)
        # With filter
        hits_myth = mem_recall.search("snake or tree", top_k=5, source_id="myth")
        assert all(h.source_id == "myth" for h in hits_myth)
        assert len(hits_myth) <= len(hits)

    def test_search_min_score_filter(self, mem_recall):
        mem_recall.add_text("Yggdrasil tree nine realms", source_id="myth")
        mem_recall.add_text("completely unrelated cooking recipe pasta", source_id="food")
        # Default min_score = 0
        hits_loose = mem_recall.search("Yggdrasil", top_k=5, min_score=0.0)
        # High threshold drops weak matches
        hits_strict = mem_recall.search("Yggdrasil", top_k=5, min_score=0.5)
        assert len(hits_strict) <= len(hits_loose)

    def test_search_returns_descending_scores(self, mem_recall):
        mem_recall.add_text("apple banana cherry", source_id="fruit")
        mem_recall.add_text("apple orange mango", source_id="fruit2")
        mem_recall.add_text("grape kiwi lemon", source_id="fruit3")
        hits = mem_recall.search("apple banana", top_k=3)
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)

    def test_search_results_have_chunk(self, mem_recall):
        mem_recall.add_text("Hello world this is Yggdrasil", source_id="d1")
        hits = mem_recall.search("Yggdrasil", top_k=1)
        assert hits[0].chunk is not None
        assert "Yggdrasil" in hits[0].chunk.text

    def test_search_score_is_float_in_range(self, mem_recall):
        mem_recall.add_text("alpha beta gamma", source_id="d")
        hits = mem_recall.search("alpha", top_k=1)
        assert -1.0 <= hits[0].score <= 1.0

    def test_search_requester_passes_through_read_guard(self, mem_recall):
        mem_recall.add_text("odin sees ravens", source_id="odin", metadata={"agent": "odin"})
        mem_recall.add_text("mimir guards wisdom", source_id="mimir", metadata={"agent": "mimir"})
        seen_requesters = []

        def policy(hit, requester):
            seen_requesters.append(requester)
            return hit.chunk.metadata.get("agent") == requester

        hits = mem_recall.search("odin mimir", top_k=5, requester="odin", policy=policy)
        assert hits
        assert all(hit.chunk.metadata["agent"] == "odin" for hit in hits)
        assert set(seen_requesters) == {"odin"}

    def test_search_scope_filters_metadata(self, mem_recall):
        mem_recall.add_text("public yggdrasil", source_id="a", metadata={"scope": "public"})
        mem_recall.add_text("private yggdrasil", source_id="b", metadata={"scope": "private"})
        hits = mem_recall.search("yggdrasil", top_k=5, scope="public")
        assert hits
        assert all(hit.chunk.metadata["scope"] == "public" for hit in hits)


class TestVectorRecallGet:
    """Direct fetch by chunk_id."""

    def test_get_existing(self, mem_recall, make_chunk):
        c = make_chunk("hello world")
        mem_recall.add(c, source_id="d1")
        hit = mem_recall.get(c.id)
        assert hit is not None
        assert hit.chunk.id == c.id
        assert hit.source_id == "d1"

    def test_get_missing(self, mem_recall):
        assert mem_recall.get("nonexistent") is None

    def test_get_score_is_one(self, mem_recall, make_chunk):
        # Direct fetch returns score=1.0 to signal "exact match"
        c = make_chunk("hello")
        mem_recall.add(c, source_id="d")
        assert mem_recall.get(c.id).score == 1.0


class TestVectorRecallSources:
    """list_sources and stats."""

    def test_list_sources_empty(self, mem_recall):
        assert mem_recall.list_sources() == []

    def test_list_sources_counts(self, mem_recall):
        mem_recall.add_text("one two", source_id="a")
        mem_recall.add_text("three four", source_id="a")
        mem_recall.add_text("five six", source_id="b")
        sources = mem_recall.list_sources()
        assert sources == [
            {"source_id": "a", "count": 2},
            {"source_id": "b", "count": 1},
        ]

    def test_stats(self, mem_recall):
        mem_recall.add_text("hello world", source_id="d1")
        mem_recall.add_text("another doc", source_id="d2")
        s = mem_recall.stats()
        assert s["vectors"] == 2
        assert s["sources"] == 2
        assert s["avg_chars"] > 0
        assert s["embedder_dim"] == 64

    def test_stats_empty(self, mem_recall):
        s = mem_recall.stats()
        assert s["vectors"] == 0
        assert s["sources"] == 0
        assert s["avg_chars"] == 0.0


class TestVectorRecallDimMismatch:
    """Robustness when the stored dim differs from current embedder."""

    def test_search_skips_wrong_dim(self, tmp_db):
        # Write with dim=64
        e1 = HashEmbedder(dim=64)
        r1 = VectorRecall(tmp_db, embedder=e1)
        c = Chunk(
            id="c1",
            text="hello",
            index=0,
            start_offset=0,
            end_offset=5,
            strategy="test",
            metadata={},
            token_estimate=1,
        )
        r1.add(c, source_id="d")
        # Reopen with dim=128 (different) — should skip on search
        e2 = HashEmbedder(dim=128)
        r2 = VectorRecall(tmp_db, embedder=e2)
        hits = r2.search("hello", top_k=5)
        assert hits == []


# ── RecallHit ───────────────────────────────────────────────────────────


class TestRecallHit:
    """to_dict serialization."""

    def test_to_dict_keys(self, mem_recall, make_chunk):
        c = make_chunk("hello world", idx=2, source="d1")
        mem_recall.add(c, source_id="d1")
        hit = mem_recall.get(c.id)
        d = hit.to_dict()
        assert "chunk" in d
        assert "score" in d
        assert "vector_id" in d
        assert "source_id" in d
        assert d["source_id"] == "d1"
        assert d["chunk"]["id"] == c.id
        assert d["chunk"]["text"] == "hello world"


# ── chunk_and_recall helper ─────────────────────────────────────────────


class TestChunkAndRecall:
    """The one-shot helper function."""

    def test_end_to_end(self, tmp_db):
        docs = {
            "norse": "Yggdrasil is the world tree. Odin is the Allfather.",
            "tech": "Python is a programming language created by Guido van Rossum.",
            "food": "Pizza is a savory Italian dish with tomato and cheese.",
        }
        hits = chunk_and_recall(tmp_db, docs, "Tell me about Norse mythology", top_k=3)
        assert len(hits) > 0
        assert hits[0].source_id == "norse"

    def test_end_to_end_with_custom_embedder(self, tmp_db):
        e = HashEmbedder(dim=128, ngram_min=1, ngram_max=2)
        hits = chunk_and_recall(
            tmp_db,
            {"a": "hello world", "b": "goodbye world"},
            "hello",
            top_k=2,
            embedder=e,
        )
        assert len(hits) > 0
        assert hits[0].source_id == "a"

    def test_end_to_end_creates_persistent_db(self, tmp_db):
        chunk_and_recall(tmp_db, {"d": "hello"}, "hi", top_k=1)
        assert os.path.exists(tmp_db)
        # Reopen
        e = HashEmbedder(dim=1024)
        r = VectorRecall(tmp_db, embedder=e)
        # Stored vectors are dim=1024 (helper's default)
        assert r.count() == 1


# ── Integration: long text + multi-chunk ────────────────────────────────


class TestLongDocumentIntegration:
    """End-to-end behavior on realistic long inputs."""

    LONG_NORSE = (
        "Yggdrasil is the immense and sacred tree at the center of the cosmos "
        "in Norse mythology. It connects the nine worlds, including Asgard, "
        "Midgard, Helheim, and others. At its base are three roots that reach "
        "into different realms. An eagle sits at the top of Yggdrasil, and a "
        "dragon named Nidhogg gnaws at its roots. The tree is central to the "
        "beliefs of the Norse people and represents the structure of reality. "
        "Odin, the Allfather, is said to have hung himself from Yggdrasil for "
        "nine days and nights in order to gain wisdom. The Norns — three "
        "female beings — pour water from a well onto the tree each day to "
        "keep it alive. Without Yggdrasil, the worlds would fall apart."
    )

    LONG_PYTHON = (
        "Python is a high-level, general-purpose programming language. Its "
        "design philosophy emphasizes code readability and the use of "
        "significant indentation. Python is dynamically typed and "
        "garbage-collected. It supports multiple programming paradigms, "
        "including structured, object-oriented, and functional programming. "
        "The standard library is large and comprehensive. Python was created "
        "by Guido van Rossum and first released in 1991. It is used in web "
        "development, data science, machine learning, and many other fields. "
        "Popular frameworks include Django, Flask, FastAPI, NumPy, pandas, "
        "and PyTorch. Python's package manager pip is the de facto standard "
        "for installing third-party packages from PyPI, the Python Package "
        "Index."
    )

    LONG_RECIPE = (
        "Spaghetti carbonara is a traditional Italian pasta dish from Rome. "
        "It is made with eggs, hard cheese, cured pork (guanciale), and "
        "black pepper. The cheese is usually Pecorino Romano. Some recipes "
        "substitute pancetta for guanciale, but purists insist on the "
        "original. The dish is prepared by combining the hot pasta with a "
        "raw egg and cheese mixture, which cooks gently from the residual "
        "heat. Cream is NOT a traditional ingredient, despite what many "
        "outside Italy believe. The finished dish should be creamy without "
        "actual cream. It is typically served as a primo piatto (first "
        "course) in a multi-course Italian meal."
    )

    def test_topic_matching(self, mem_recall):
        r = mem_recall
        r.add_text(self.LONG_NORSE, source_id="norse")
        r.add_text(self.LONG_PYTHON, source_id="python")
        r.add_text(self.LONG_RECIPE, source_id="recipe")
        hits = r.search("What is the world tree in Norse mythology?", top_k=3)
        assert hits[0].source_id == "norse"

        hits = r.search("How do I cook Italian pasta with eggs?", top_k=3)
        assert hits[0].source_id == "recipe"

        hits = r.search("What language did Guido create?", top_k=3)
        assert hits[0].source_id == "python"

    def test_chunker_chunked_long_doc(self, mem_recall):
        # Use a small target_size so this produces multiple chunks
        r = VectorRecall(
            ":memory:",
            embedder=HashEmbedder(dim=128),
            chunker=SemanticChunker(target_size=200, overlap=20),
        )
        r.add_text(self.LONG_NORSE, source_id="norse")
        # Multiple chunks were indexed
        assert r.count() > 1

    def test_relevance_ordering_with_overlap(self, mem_recall):
        r = mem_recall
        # Highly relevant
        r.add_text(
            "Yggdrasil connects the nine worlds in Norse cosmology. "
            "It is the world tree.",
            source_id="a",
        )
        # Tangentially relevant (shares one word)
        r.add_text(
            "Trees provide oxygen and shade in forests around the world.",
            source_id="b",
        )
        # Unrelated
        r.add_text(
            "Quantum mechanics describes subatomic particle behavior.",
            source_id="c",
        )
        hits = r.search("What is Yggdrasil the world tree?", top_k=3)
        assert hits[0].source_id == "a"
        # The unrelated one should rank last (or at least not first)
        sources = [h.source_id for h in hits]
        assert "a" in sources
        assert "c" in sources  # it's there, just low-scoring
