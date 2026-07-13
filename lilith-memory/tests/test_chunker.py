"""Tests for lilith_memory.chunker (SemanticChunker + strategies)."""

from __future__ import annotations

import pytest

from lilith_memory.chunker import (
    Chunk,
    ChunkStrategy,
    SemanticChunker,
    chunk_text,
)


# ── Constructor validation ─────────────────────────────────────────────


def test_constructor_defaults():
    c = SemanticChunker()
    assert c.target_size == 1024
    assert c.overlap == 128
    assert c.default_strategy == ChunkStrategy.AUTO


def test_constructor_rejects_zero_target_size():
    with pytest.raises(ValueError, match="target_size"):
        SemanticChunker(target_size=0)


def test_constructor_rejects_negative_overlap():
    with pytest.raises(ValueError, match="overlap"):
        SemanticChunker(target_size=100, overlap=-1)


def test_constructor_rejects_overlap_equals_target():
    with pytest.raises(ValueError, match="overlap"):
        SemanticChunker(target_size=100, overlap=100)


def test_constructor_rejects_overlap_greater_than_target():
    with pytest.raises(ValueError, match="overlap"):
        SemanticChunker(target_size=100, overlap=200)


# ── Empty / trivial inputs ─────────────────────────────────────────────


def test_chunk_empty_text_returns_empty():
    chunker = SemanticChunker()
    assert chunker.chunk("") == []


def test_chunk_whitespace_only_returns_empty_for_paragraph():
    chunker = SemanticChunker(default_strategy=ChunkStrategy.PARAGRAPH)
    assert chunker.chunk("   \n\n   ") == []


def test_chunk_text_convenience_empty():
    assert chunk_text("") == []


# ── Character strategy ─────────────────────────────────────────────────


def test_character_chunker_basic():
    text = "a" * 100
    chunker = SemanticChunker(target_size=30, overlap=10, default_strategy=ChunkStrategy.CHARACTER)
    chunks = chunker.chunk(text)
    assert len(chunks) >= 3
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.strategy == "character"
        assert c.id.startswith("chunk_")
        assert c.token_estimate > 0


def test_character_chunker_preserves_content():
    text = "abcdefghij" * 10  # 100 chars
    chunker = SemanticChunker(
        target_size=25, overlap=5, default_strategy=ChunkStrategy.CHARACTER
    )
    chunks = chunker.chunk(text)
    # Reconstruct: all text should be accounted for
    total = sum(len(c.text) for c in chunks)
    assert total >= len(text)  # overlap means >= is correct


def test_character_chunker_single_chunk_for_short_text():
    text = "short"
    chunker = SemanticChunker(target_size=100, overlap=10, default_strategy=ChunkStrategy.CHARACTER)
    chunks = chunker.chunk(text)
    assert len(chunks) == 1
    assert chunks[0].text == "short"


def test_character_chunker_invalid_params():
    with pytest.raises(ValueError):
        SemanticChunker(target_size=10, overlap=10)


# ── Sentence strategy ──────────────────────────────────────────────────


def test_sentence_chunker_packs_sentences():
    text = (
        "This is sentence one. "
        "This is sentence two. "
        "This is sentence three. "
        "This is sentence four. "
        "This is sentence five."
    )
    chunker = SemanticChunker(
        target_size=60, overlap=10, default_strategy=ChunkStrategy.SENTENCE
    )
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1
    for c in chunks:
        assert c.strategy == "sentence"
        assert "sentence_count" in c.metadata
        assert c.metadata["sentence_count"] >= 1


def test_sentence_chunker_handles_question_marks():
    text = "What is Python? It is a language. Do you like it? Yes, I do."
    chunker = SemanticChunker(
        target_size=30, overlap=5, default_strategy=ChunkStrategy.SENTENCE
    )
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1


def test_sentence_chunker_handles_exclamations():
    text = "Hello world! How are you? I am fine. Great to hear it."
    chunker = SemanticChunker(
        target_size=25, overlap=5, default_strategy=ChunkStrategy.SENTENCE
    )
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1


# ── Paragraph strategy ─────────────────────────────────────────────────


def test_paragraph_chunker_splits_on_blank_lines():
    text = "Para one line 1.\nPara one line 2.\n\nPara two.\n\nPara three."
    chunker = SemanticChunker(
        target_size=100, overlap=0, default_strategy=ChunkStrategy.PARAGRAPH
    )
    chunks = chunker.chunk(text)
    # All paragraphs should fit in one chunk
    assert len(chunks) == 1
    assert "Para one" in chunks[0].text
    assert "Para two" in chunks[0].text
    assert "Para three" in chunks[0].text


def test_paragraph_chunker_packs_oversize_paragraphs():
    paras = []
    for i in range(5):
        paras.append("P" * 50)
    text = "\n\n".join(paras)
    chunker = SemanticChunker(
        target_size=120, overlap=0, default_strategy=ChunkStrategy.PARAGRAPH
    )
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.strategy == "paragraph"


# ── Code strategy ──────────────────────────────────────────────────────


def test_code_chunker_splits_python_functions():
    code = '''def foo():
    """Docstring."""
    return 1

def bar():
    """Another docstring."""
    x = 2
    return x

class Baz:
    """A class."""

    def method(self):
        return 3
'''
    chunker = SemanticChunker(
        target_size=500, overlap=0, default_strategy=ChunkStrategy.CODE
    )
    chunks = chunker.chunk(code)
    assert len(chunks) >= 3
    for c in chunks:
        assert c.strategy == "code"
        assert "ast_type" in c.metadata
    # The class should be one chunk
    class_chunks = [c for c in chunks if c.metadata.get("ast_type") == "ClassDef"]
    assert len(class_chunks) == 1


def test_code_chunker_oversize_function_recurses():
    # Single huge function > target_size
    body = "    x = 1\n" * 200
    code = f"def huge():\n{body}"
    chunker = SemanticChunker(
        target_size=100, overlap=10, default_strategy=ChunkStrategy.CODE
    )
    chunks = chunker.chunk(code)
    # Should not lose the data — at least one chunk must exist
    assert len(chunks) >= 1


def test_code_chunker_falls_back_to_lines_for_syntax_errors():
    bad_code = "def broken(:\n    invalid python !!"
    chunker = SemanticChunker(
        target_size=20, overlap=5, default_strategy=ChunkStrategy.CODE
    )
    chunks = chunker.chunk(bad_code)
    assert len(chunks) >= 1
    for c in chunks:
        assert c.strategy == "code"
        assert "language" in c.metadata


def test_code_chunker_module_header():
    code = '"""Module docstring."""\n\nimport os\n\ndef f():\n    return 1\n'
    chunker = SemanticChunker(
        target_size=500, overlap=0, default_strategy=ChunkStrategy.CODE
    )
    chunks = chunker.chunk(code)
    # ModuleHeader chunk should be first
    assert chunks[0].metadata.get("ast_type") == "ModuleHeader"


# ── Markdown strategy ──────────────────────────────────────────────────


def test_markdown_chunker_splits_on_headers():
    md = """# Title

Intro text.

## Section 1

Content 1.

## Section 2

Content 2.
"""
    chunker = SemanticChunker(
        target_size=500, overlap=0, default_strategy=ChunkStrategy.MARKDOWN
    )
    chunks = chunker.chunk(md)
    assert len(chunks) >= 1
    for c in chunks:
        assert c.strategy == "markdown"
        assert "sections" in c.metadata


def test_markdown_chunker_preserves_hierarchy():
    md = """# H1

## H2a

content a

### H3a

content a3

## H2b

content b
"""
    chunker = SemanticChunker(
        target_size=200, overlap=0, default_strategy=ChunkStrategy.MARKDOWN
    )
    chunks = chunker.chunk(md)
    # First chunk should include H1 / H2a / H3a
    full_text = "\n".join(c.text for c in chunks)
    assert "H1" in full_text
    assert "H2a" in full_text
    assert "H2b" in full_text


def test_markdown_chunker_handles_preamble():
    md = """This is preamble text.

# Header

Body.
"""
    chunker = SemanticChunker(
        target_size=500, overlap=0, default_strategy=ChunkStrategy.MARKDOWN
    )
    chunks = chunker.chunk(md)
    # Preamble is included in metadata sections. ``sections`` is a list
    # of header paths (each path is itself a list of strings), so we
    # flatten one level.
    all_section_paths: list[list[str]] = []
    for c in chunks:
        all_section_paths.extend(c.metadata.get("sections", []))
    # Find the path that contains the preamble marker
    assert any("(preamble)" in path for path in all_section_paths)


def test_markdown_chunker_falls_back_when_no_headers():
    md = "Just a paragraph.\n\nAnother paragraph."
    chunker = SemanticChunker(
        target_size=500, overlap=0, default_strategy=ChunkStrategy.MARKDOWN
    )
    chunks = chunker.chunk(md)
    # No headers → paragraph fallback
    assert len(chunks) >= 1


# ── Conversation strategy ──────────────────────────────────────────────


def test_conversation_chunker_splits_on_user_assistant_turns():
    convo = """User: Hello there.
How are you?

Assistant: I am fine, thanks!

User: Great to hear.

Assistant: Anything else I can help with?
"""
    chunker = SemanticChunker(
        target_size=500, overlap=0, default_strategy=ChunkStrategy.CONVERSATION
    )
    chunks = chunker.chunk(convo)
    assert len(chunks) >= 1
    for c in chunks:
        assert c.strategy == "conversation"
        assert "turns" in c.metadata


def test_conversation_chunker_recognises_json_roles():
    convo = """{"role": "user", "content": "Hi"}
{"role": "assistant", "content": "Hello!"}
{"role": "user", "content": "How are you?"}
{"role": "assistant", "content": "Doing well, thanks."}
"""
    chunker = SemanticChunker(
        target_size=200, overlap=0, default_strategy=ChunkStrategy.CONVERSATION
    )
    chunks = chunker.chunk(convo)
    assert len(chunks) >= 1


# ── Auto detection ─────────────────────────────────────────────────────


def test_auto_detects_code():
    from lilith_memory.chunker import _detect_strategy
    code = "def foo():\n    return 1\n\nclass Bar:\n    pass\n"
    assert _detect_strategy(code) == ChunkStrategy.CODE


def test_auto_detects_markdown():
    from lilith_memory.chunker import _detect_strategy
    md = "# Title\n\nSome text.\n\n## Sub\n\nMore."
    assert _detect_strategy(md) == ChunkStrategy.MARKDOWN


def test_auto_detects_conversation():
    from lilith_memory.chunker import _detect_strategy
    convo = "User: hi\n\nAssistant: hello\n"
    assert _detect_strategy(convo) == ChunkStrategy.CONVERSATION


def test_auto_detects_paragraph():
    from lilith_memory.chunker import _detect_strategy
    text = "Para one.\n\nPara two.\n\nPara three."
    assert _detect_strategy(text) == ChunkStrategy.PARAGRAPH


def test_auto_detects_sentence():
    from lilith_memory.chunker import _detect_strategy
    text = "First sentence. Second sentence. Third sentence. Fourth. Fifth."
    assert _detect_strategy(text) == ChunkStrategy.SENTENCE


def test_auto_detects_character_for_short_text():
    from lilith_memory.chunker import _detect_strategy
    text = "short text without clear structure"
    assert _detect_strategy(text) == ChunkStrategy.CHARACTER


def test_auto_dispatch_uses_detection():
    code = "def foo():\n    return 1\n"
    chunker = SemanticChunker(target_size=500, overlap=0, default_strategy=ChunkStrategy.AUTO)
    chunks = chunker.chunk(code)
    assert chunks[0].strategy == "code"


# ── Metadata enrichment ────────────────────────────────────────────────


def test_source_id_added_to_metadata():
    chunker = SemanticChunker(default_strategy=ChunkStrategy.CHARACTER)
    chunks = chunker.chunk("hello world", source_id="doc-1")
    assert all(c.metadata.get("source_id") == "doc-1" for c in chunks)


def test_extra_metadata_merged_without_overwrite():
    chunker = SemanticChunker(default_strategy=ChunkStrategy.CHARACTER)
    chunks = chunker.chunk("hello", extra_metadata={"author": "skadi", "strategy": "CUSTOM"})
    for c in chunks:
        assert c.metadata.get("author") == "skadi"
        # strategy key from extra_metadata shouldn't overwrite chunk's own
        # (setdefault semantics)
        assert c.strategy in ("character",)


def test_chunk_uuid_in_metadata():
    chunker = SemanticChunker(default_strategy=ChunkStrategy.CHARACTER)
    chunks = chunker.chunk("hello world")
    for c in chunks:
        assert "chunk_uuid" in c.metadata
        assert len(c.metadata["chunk_uuid"]) > 0


# ── Chunk dataclass ────────────────────────────────────────────────────


def test_chunk_to_dict_serialization():
    chunker = SemanticChunker(default_strategy=ChunkStrategy.CHARACTER)
    chunks = chunker.chunk("hello world")
    d = chunks[0].to_dict()
    assert d["text"] == chunks[0].text
    assert d["strategy"] == "character"
    assert d["id"] == chunks[0].id


def test_chunk_offsets_are_valid():
    text = "abcdefghij" * 5  # 50 chars
    chunker = SemanticChunker(
        target_size=20, overlap=5, default_strategy=ChunkStrategy.CHARACTER
    )
    chunks = chunker.chunk(text)
    for c in chunks:
        assert 0 <= c.start_offset < len(text) or c.start_offset == 0
        assert c.end_offset > c.start_offset


# ── Batch chunking ─────────────────────────────────────────────────────


def test_chunk_batch_returns_list_of_lists():
    chunker = SemanticChunker(default_strategy=ChunkStrategy.CHARACTER)
    texts = ["short text", "another piece", "third text here"]
    results = chunker.chunk_batch(texts)
    assert len(results) == 3
    for r in results:
        assert isinstance(r, list)
        assert len(r) >= 1


def test_chunk_batch_handles_empty_strings():
    chunker = SemanticChunker(default_strategy=ChunkStrategy.CHARACTER)
    results = chunker.chunk_batch(["", "hello", ""])
    assert results[0] == []
    assert len(results[1]) >= 1
    assert results[2] == []


# ── Statistics ─────────────────────────────────────────────────────────


def test_stats_for_empty_list():
    chunker = SemanticChunker()
    s = chunker.stats([])
    assert s["count"] == 0
    assert s["total_chars"] == 0
    assert s["strategies"] == {}


def test_stats_for_chunks():
    chunker = SemanticChunker(target_size=50, overlap=10, default_strategy=ChunkStrategy.CHARACTER)
    chunks = chunker.chunk("a" * 200)
    s = chunker.stats(chunks)
    assert s["count"] == len(chunks)
    assert s["total_chars"] >= 200  # overlap inflates
    assert "character" in s["strategies"]
    assert s["avg_chunk_chars"] > 0


def test_stats_strategy_breakdown():
    chunker = SemanticChunker()
    # Use direct char chunks
    c1 = chunker.chunk("a" * 100, strategy=ChunkStrategy.CHARACTER)
    c2 = chunker.chunk("a" * 100, strategy=ChunkStrategy.SENTENCE)
    all_chunks = c1 + c2
    s = chunker.stats(all_chunks)
    assert s["strategies"].get("character", 0) >= 1
    assert s["strategies"].get("sentence", 0) >= 1


# ── chunk_text convenience ─────────────────────────────────────────────


def test_chunk_text_uses_auto_by_default():
    code = "def foo():\n    return 1\n"
    chunks = chunk_text(code, target_size=200, overlap=0)
    assert chunks[0].strategy == "code"


def test_chunk_text_explicit_strategy():
    text = "P1\n\nP2\n\nP3"
    chunks = chunk_text(text, target_size=200, overlap=0, strategy=ChunkStrategy.PARAGRAPH)
    assert chunks[0].strategy == "paragraph"


# ── Long text smoke test ───────────────────────────────────────────────


def test_long_text_character_chunking():
    text = "The quick brown fox jumps over the lazy dog. " * 100
    chunker = SemanticChunker(
        target_size=200, overlap=20, default_strategy=ChunkStrategy.CHARACTER
    )
    chunks = chunker.chunk(text)
    assert len(chunks) >= 5
    s = chunker.stats(chunks)
    assert s["count"] >= 5


def test_long_text_code_chunking():
    parts = []
    for i in range(20):
        parts.append(f"def func_{i}():\n    return {i}\n")
    code = "\n".join(parts)
    chunker = SemanticChunker(
        target_size=200, overlap=0, default_strategy=ChunkStrategy.CODE
    )
    chunks = chunker.chunk(code)
    assert len(chunks) == 20


# ── Edge cases ─────────────────────────────────────────────────────────


def test_chunk_with_only_whitespace_in_code_mode():
    chunker = SemanticChunker(default_strategy=ChunkStrategy.CODE)
    chunks = chunker.chunk("   \n\n  \t  \n")
    # Whitespace-only → empty chunks
    assert chunks == []


def test_chunk_single_character():
    chunker = SemanticChunker(default_strategy=ChunkStrategy.CHARACTER)
    chunks = chunker.chunk("x")
    assert len(chunks) == 1
    assert chunks[0].text == "x"


def test_chunk_unicode_text():
    text = "Hola, ¿cómo estás? Estoy bien. ¡Muy bien! ¿Y tú?"
    chunker = SemanticChunker(
        target_size=30, overlap=5, default_strategy=ChunkStrategy.SENTENCE
    )
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1


def test_chunk_large_paragraph_packs_into_multiple():
    text = ("This is a long paragraph. " * 50).strip()
    chunker = SemanticChunker(
        target_size=100, overlap=10, default_strategy=ChunkStrategy.PARAGRAPH
    )
    chunks = chunker.chunk(text)
    # Single paragraph, but too long → fall back to char packing
    assert len(chunks) >= 2


def test_chunk_idempotent_for_same_input():
    chunker = SemanticChunker(target_size=100, overlap=10, default_strategy=ChunkStrategy.CHARACTER)
    a = chunker.chunk("hello world")
    b = chunker.chunk("hello world")
    assert [c.id for c in a] == [c.id for c in b]
