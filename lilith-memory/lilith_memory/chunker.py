"""Semantic text chunker for RAG and long-memory storage.

Inspired by Neurosurfer's ``neurosurfer.rag.chunker.Chunker``: a pluggable,
strategy-driven text chunker that breaks long content into semantically
meaningful chunks with optional overlap. Designed to be the upstream step
for embedding generation, vector recall, and chunk-level memory storage.

Strategies (selected by strategy name or auto-detected from content):
    - ``character``: fixed-size character windows with overlap.
    - ``sentence``: split on sentence boundaries (period, ?, !), then
      pack into chunks of target size with overlap.
    - ``paragraph``: split on blank lines (Markdown / prose friendly).
    - ``code``: split on Python AST top-level definitions (functions,
      classes, module statements) — falls back to line-based for
      non-Python code.
    - ``markdown``: header-aware splitting (H1-H6) then paragraph
      packing — preserves logical sections.
    - ``conversation``: split on role turns (User:/Assistant: prefix
      or JSON-style ``{"role": ...}`` lines).

All strategies are pure functions that return a list of :class:`Chunk`
objects with text, start/end offsets, strategy name, and a metadata dict.

Usage::

    from lilith_memory.chunker import SemanticChunker, ChunkStrategy

    chunker = SemanticChunker(target_size=512, overlap=64)
    chunks = chunker.chunk(long_text, strategy=ChunkStrategy.AUTO)
    for c in chunks:
        embed(c.text)
        vector_store.upsert(c.id, c.text, c.metadata)
"""

from __future__ import annotations

import ast
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ── Strategy enum ─────────────────────────────────────────────────────────


class ChunkStrategy(str, Enum):
    """Available chunking strategies."""

    AUTO = "auto"            # Detect based on content
    CHARACTER = "character"  # Fixed character windows
    SENTENCE = "sentence"    # Sentence-boundary aware
    PARAGRAPH = "paragraph"  # Blank-line split
    CODE = "code"            # Python AST or line-based fallback
    MARKDOWN = "markdown"    # Header-aware section splitter
    CONVERSATION = "conversation"  # Role-turn aware


# ── Chunk dataclass ──────────────────────────────────────────────────────


@dataclass
class Chunk:
    """A single chunk produced by SemanticChunker.

    Attributes:
        id: Stable identifier (sha256 of text + index). Use as the
            primary key in vector stores.
        text: The chunk text.
        index: Zero-based ordinal in the source document.
        start_offset: Character offset in the source where this chunk
            begins (best-effort, may be approximate for AST strategies).
        end_offset: Character offset where this chunk ends.
        strategy: Name of the strategy that produced this chunk.
        metadata: Free-form dict; strategies populate extra context
            (header path, AST node type, role, etc.).
        token_estimate: Rough token count estimate (~4 chars/token).
    """

    id: str
    text: str
    index: int
    start_offset: int
    end_offset: int
    strategy: str
    metadata: dict[str, Any] = field(default_factory=dict)
    token_estimate: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict (for vector-store payloads)."""
        return {
            "id": self.id,
            "text": self.text,
            "index": self.index,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "strategy": self.strategy,
            "metadata": self.metadata,
            "token_estimate": self.token_estimate,
        }


# ── Strategy implementations ─────────────────────────────────────────────

# Approximate chars per token — used for token_estimate only.
_CHARS_PER_TOKEN = 4

# Sentence boundary regex (period, ?, ! followed by whitespace or EOL).
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡\"'(\[])")
# Paragraph boundary: two or more newlines.
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")
# Markdown header detection.
_MARKDOWN_HEADER = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
# Conversation role-turn prefix.
_ROLE_TURN = re.compile(
    r"^(?:(User|Human|Assistant|AI|System|SystemPrompt|Bot)|"
    r'\{?"role"?\s*:\s*"(user|assistant|system)")\s*[:\s]',
    re.IGNORECASE | re.MULTILINE,
)


def _estimate_tokens(text: str) -> int:
    """Return a rough token count estimate."""
    return max(1, len(text) // _CHARS_PER_TOKEN) if text else 0


def _make_id(text: str, index: int) -> str:
    """Stable chunk id derived from text content + ordinal."""
    h = hashlib.sha256(f"{index}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"chunk_{index:04d}_{h}"


def _char_chunks(text: str, target_size: int, overlap: int) -> list[Chunk]:
    """Fixed-size character windows with overlap."""
    if not text or not text.strip():
        return []
    if target_size <= 0:
        raise ValueError(f"target_size must be > 0, got {target_size}")
    if overlap < 0 or overlap >= target_size:
        raise ValueError(
            f"overlap must be in [0, target_size), got {overlap} vs {target_size}"
        )
    chunks: list[Chunk] = []
    step = target_size - overlap
    pos = 0
    idx = 0
    n = len(text)
    while pos < n:
        end = min(pos + target_size, n)
        piece = text[pos:end]
        chunks.append(
            Chunk(
                id=_make_id(piece, idx),
                text=piece,
                index=idx,
                start_offset=pos,
                end_offset=end,
                strategy=ChunkStrategy.CHARACTER.value,
                token_estimate=_estimate_tokens(piece),
            )
        )
        idx += 1
        if end == n:
            break
        pos += step
    return chunks


def _sentence_chunks(text: str, target_size: int, overlap: int) -> list[Chunk]:
    """Sentence-boundary aware chunker with pack-and-overlap."""
    if not text:
        return []
    sentences = _SENTENCE_SPLIT.split(text.strip())
    sentences = [s.strip() for s in sentences if s and s.strip()]
    if not sentences:
        return _char_chunks(text, target_size, overlap)

    # Pack sentences into chunks of approx target_size characters.
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_len = 0
    cursor = 0  # tracks start offset in original text

    def _flush(buf: list[str], start: int, end: int, idx: int) -> Chunk:
        body = " ".join(buf).strip()
        return Chunk(
            id=_make_id(body, idx),
            text=body,
            index=idx,
            start_offset=start,
            end_offset=end,
            strategy=ChunkStrategy.SENTENCE.value,
            token_estimate=_estimate_tokens(body),
            metadata={"sentence_count": len(buf)},
        )

    idx = 0
    start_off = 0
    for sent in sentences:
        sent_len = len(sent) + 1  # +1 for the joining space
        if buf and (buf_len + sent_len > target_size):
            # Flush current buffer
            chunk = _flush(buf, start_off, cursor, idx)
            chunks.append(chunk)
            idx += 1
            # Compute overlap: keep last few sentences that fit in overlap
            if overlap > 0:
                keep: list[str] = []
                keep_len = 0
                for prev in reversed(buf):
                    pl = len(prev) + 1
                    if keep_len + pl > overlap:
                        break
                    keep.insert(0, prev)
                    keep_len += pl
                buf = keep
                buf_len = sum(len(s) + 1 for s in buf)
                # Recompute start_off: best-effort using text.find
                if buf:
                    start_off = max(0, text.find(buf[0], cursor - len(buf[0]) - 10))
                else:
                    start_off = cursor
            else:
                buf = []
                buf_len = 0
                start_off = cursor
        buf.append(sent)
        buf_len += sent_len
        cursor += sent_len

    if buf:
        chunks.append(_flush(buf, start_off, cursor, idx))

    return chunks


def _paragraph_chunks(text: str, target_size: int, overlap: int) -> list[Chunk]:
    """Paragraph-based chunker — splits on blank lines, then packs."""
    if not text:
        return []
    paragraphs = _PARAGRAPH_SPLIT.split(text.strip())
    paragraphs = [p.strip() for p in paragraphs if p and p.strip()]
    if not paragraphs:
        return _char_chunks(text, target_size, overlap)

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_len = 0
    idx = 0
    start_off = 0
    cursor = 0

    def _flush(buf: list[str], start: int, end: int, idx: int) -> Chunk:
        body = "\n\n".join(buf).strip()
        return Chunk(
            id=_make_id(body, idx),
            text=body,
            index=idx,
            start_offset=start,
            end_offset=end,
            strategy=ChunkStrategy.PARAGRAPH.value,
            token_estimate=_estimate_tokens(body),
            metadata={"paragraph_count": len(buf)},
        )

    for para in paragraphs:
        para_len = len(para) + 2  # +2 for the joining "\n\n"
        if buf and (buf_len + para_len > target_size):
            chunks.append(_flush(buf, start_off, cursor, idx))
            idx += 1
            buf = []
            buf_len = 0
            start_off = cursor
        if not buf:
            start_off = text.find(para, max(0, cursor - 1))
            if start_off < 0:
                start_off = cursor
        buf.append(para)
        buf_len += para_len
        cursor = start_off + len(para) + 2

    if buf:
        chunks.append(_flush(buf, start_off, cursor, idx))

    # If a single paragraph exceeded target_size and was kept whole,
    # split it recursively via char chunking so no data is lost.
    final: list[Chunk] = []
    for c in chunks:
        if c.metadata.get("paragraph_count", 0) == 1 and len(c.text) > target_size:
            sub = _char_chunks(c.text, target_size, overlap)
            for s in sub:
                s.metadata["parent_strategy"] = ChunkStrategy.PARAGRAPH.value
                s.strategy = ChunkStrategy.PARAGRAPH.value
            if sub:
                final.extend(sub)
            else:
                final.append(c)
        else:
            final.append(c)
    # Re-number after expansion
    for i, c in enumerate(final):
        c.index = i
        c.id = _make_id(c.text, i)
    return final


def _code_chunks(text: str, target_size: int, overlap: int) -> list[Chunk]:
    """Python AST-aware chunker. Falls back to line-based for non-Python.

    Each top-level function, class, or statement becomes one chunk. If
    a single node exceeds ``target_size``, it is recursively split.
    """
    if not text:
        return []

    # Attempt to parse as Python
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Fall back to line-based chunking
        return _line_chunks(text, target_size, overlap, language="unknown")

    chunks: list[Chunk] = []
    lines = text.splitlines(keepends=True)

    def _line_to_offset(line_no: int) -> int:
        """Convert 1-indexed line number to character offset."""
        off = 0
        for i, ln in enumerate(lines[: max(0, line_no - 1)]):
            off += len(ln)
        return off

    def _emit(node: ast.AST) -> None:
        if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
            return
        start_line = node.lineno
        end_line = getattr(node, "end_lineno", start_line)
        if end_line < start_line:
            end_line = start_line
        node_text = "".join(lines[start_line - 1: end_line])
        start_off = _line_to_offset(start_line)
        end_off = _line_to_offset(end_line + 1)

        if len(node_text) <= target_size:
            kind = type(node).__name__
            chunks.append(
                Chunk(
                    id=_make_id(node_text, len(chunks)),
                    text=node_text,
                    index=len(chunks),
                    start_offset=start_off,
                    end_offset=end_off,
                    strategy=ChunkStrategy.CODE.value,
                    token_estimate=_estimate_tokens(node_text),
                    metadata={"ast_type": kind, "start_line": start_line, "end_line": end_line},
                )
            )
        else:
            # Too large — recursively chunk this node's body
            for child in ast.iter_child_nodes(node):
                _emit(child)
            # If still no chunks emitted, force a character chunk of the
            # node text so we never lose data.
            if not chunks or chunks[-1].end_offset <= start_off:
                for sub in _char_chunks(node_text, target_size, overlap):
                    sub.metadata["ast_type"] = type(node).__name__
                    sub.metadata["parent_node"] = "oversize"
                    chunks.append(sub)

    for node in tree.body:
        _emit(node)

    # Module-level docstring or imports that aren't statements — emit as
    # a "module_header" chunk so we don't lose them.
    #
    # The AST places a module docstring as the first ``Expr`` node
    # (lineno=1). When such a node is just a string literal with no
    # body, it IS the header — emit it as ModuleHeader instead of Expr.
    if tree.body:
        first = tree.body[0]
        # Rewrite: if the first node is an Expr (typically the docstring)
        # and it starts at line 1, treat it as the module header.
        if (
            isinstance(first, ast.Expr)
            and getattr(first, "lineno", 99) == 1
        ):
            # Remove the Expr chunk that _emit would have produced and
            # replace it with a ModuleHeader chunk in-place.
            for c in chunks:
                if c.metadata.get("ast_type") == "Expr":
                    c.metadata["ast_type"] = "ModuleHeader"
                    break
        elif hasattr(first, "lineno") and first.lineno > 1:
            header = "".join(lines[: first.lineno - 1])
            if header.strip():
                chunks.insert(
                    0,
                    Chunk(
                        id=_make_id(header, 0),
                        text=header,
                        index=0,
                        start_offset=0,
                        end_offset=len(header),
                        strategy=ChunkStrategy.CODE.value,
                        token_estimate=_estimate_tokens(header),
                        metadata={"ast_type": "ModuleHeader"},
                    ),
                )

    # Re-number after potential prepending
    for i, c in enumerate(chunks):
        c.index = i
        c.id = _make_id(c.text, i)

    return chunks


def _line_chunks(
    text: str, target_size: int, overlap: int, language: str = "unknown"
) -> list[Chunk]:
    """Line-based fallback for the code strategy (or generic splitting)."""
    if not text:
        return []
    if target_size <= 0:
        raise ValueError(f"target_size must be > 0, got {target_size}")
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    avg_line_len = max(1, len(text) // len(lines))
    lines_per_chunk = max(1, target_size // avg_line_len)
    overlap_lines = overlap // avg_line_len if overlap > 0 else 0
    step = max(1, lines_per_chunk - overlap_lines)

    chunks: list[Chunk] = []
    offset = 0
    idx = 0
    pos = 0
    n = len(lines)
    while pos < n:
        end = min(pos + lines_per_chunk, n)
        piece = "".join(lines[pos:end])
        piece_end = offset + len(piece)
        chunks.append(
            Chunk(
                id=_make_id(piece, idx),
                text=piece,
                index=idx,
                start_offset=offset,
                end_offset=piece_end,
                strategy=ChunkStrategy.CODE.value,
                token_estimate=_estimate_tokens(piece),
                metadata={"language": language, "start_line": pos + 1, "end_line": end},
            )
        )
        idx += 1
        offset = piece_end
        if end == n:
            break
        pos += step
    return chunks


def _markdown_chunks(text: str, target_size: int, overlap: int) -> list[Chunk]:
    """Markdown header-aware section splitter.

    Splits on H1-H6 headers, then packs adjacent sections into chunks
    up to ``target_size``. Section paths are preserved in metadata.
    """
    if not text:
        return []

    # Find all header positions
    matches = list(_MARKDOWN_HEADER.finditer(text))
    if not matches:
        return _paragraph_chunks(text, target_size, overlap)

    # Build sections: (header_path, content)
    sections: list[tuple[list[str], str]] = []
    path_stack: list[tuple[int, str]] = []  # (level, title)

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()

        # Update path stack: pop deeper-or-equal levels
        while path_stack and path_stack[-1][0] >= level:
            path_stack.pop()
        path_stack.append((level, title))
        header_path = [t for _, t in path_stack]

        if body or i == 0:
            sections.append((header_path, body))

    # If content exists before the first header, treat as preamble
    if matches and matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.insert(0, (["(preamble)"], preamble))

    # Pack sections into chunks of approx target_size
    chunks: list[Chunk] = []
    buf: list[tuple[list[str], str]] = []
    buf_len = 0
    idx = 0
    start_off = 0
    cursor = 0

    def _flush(
        buf: list[tuple[list[str], str]], start: int, end: int, idx: int
    ) -> Chunk:
        # Rebuild markdown with headers for each section
        pieces: list[str] = []
        last_level = 0
        for path, body in buf:
            level = min(len(path), 6)
            header = "#" * level + " " + " / ".join(path) + "\n\n"
            pieces.append(header + body)
        body_text = "\n\n".join(pieces).strip()
        return Chunk(
            id=_make_id(body_text, idx),
            text=body_text,
            index=idx,
            start_offset=start,
            end_offset=end,
            strategy=ChunkStrategy.MARKDOWN.value,
            token_estimate=_estimate_tokens(body_text),
            metadata={"sections": [p for p, _ in buf], "section_count": len(buf)},
        )

    for path, body in sections:
        body_len = len(body) + sum(len(p) + 4 for p in path) + 4
        if buf and (buf_len + body_len > target_size):
            chunks.append(_flush(buf, start_off, cursor, idx))
            idx += 1
            buf = []
            buf_len = 0
            start_off = cursor
        if not buf:
            first_marker = "#" * min(len(path), 6) + " " + " / ".join(path)
            start_off = text.find(first_marker, max(0, cursor - 1))
            if start_off < 0:
                start_off = cursor
        buf.append((path, body))
        buf_len += body_len
        cursor = start_off + body_len

    if buf:
        chunks.append(_flush(buf, start_off, cursor, idx))

    return chunks


def _conversation_chunks(text: str, target_size: int, overlap: int) -> list[Chunk]:
    """Conversation-aware chunker — splits on role turns.

    Recognises both ``User: ...`` style and JSON-style
    ``{"role": "user", "content": "..."}`` messages. Packs turns into
    chunks that respect turn boundaries.
    """
    if not text:
        return []

    # Try to detect role turns by splitting on the regex
    matches = list(_ROLE_TURN.finditer(text))
    if not matches:
        return _paragraph_chunks(text, target_size, overlap)

    turns: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        role = (m.group(1) or m.group(2) or "user").lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        turns.append((role, body))

    chunks: list[Chunk] = []
    buf: list[tuple[str, str]] = []
    buf_len = 0
    idx = 0
    start_off = 0
    cursor = 0

    def _flush(
        buf: list[tuple[str, str]], start: int, end: int, idx: int
    ) -> Chunk:
        lines: list[str] = []
        for role, body in buf:
            label = role.capitalize()
            lines.append(f"{label}: {body}")
        body_text = "\n\n".join(lines).strip()
        return Chunk(
            id=_make_id(body_text, idx),
            text=body_text,
            index=idx,
            start_offset=start,
            end_offset=end,
            strategy=ChunkStrategy.CONVERSATION.value,
            token_estimate=_estimate_tokens(body_text),
            metadata={"turns": [{"role": r, "length": len(b)} for r, b in buf]},
        )

    for role, body in turns:
        body_len = len(body) + len(role) + 4  # "Role: ...\n\n"
        if buf and (buf_len + body_len > target_size):
            chunks.append(_flush(buf, start_off, cursor, idx))
            idx += 1
            buf = []
            buf_len = 0
            start_off = cursor
        if not buf:
            label = role.capitalize()
            start_off = text.find(f"{label}:", max(0, cursor - 1))
            if start_off < 0:
                start_off = cursor
        buf.append((role, body))
        buf_len += body_len
        cursor = start_off + body_len

    if buf:
        chunks.append(_flush(buf, start_off, cursor, idx))

    return chunks


# ── Auto-detection ───────────────────────────────────────────────────────


def _detect_strategy(text: str) -> ChunkStrategy:
    """Heuristic detection of the best strategy for a piece of text."""
    if not text:
        return ChunkStrategy.PARAGRAPH
    sample = text[:2000]  # peek at the first ~2 KB

    # Code detection: looks like Python (def/class/import) or has
    # very long lines without blank lines.
    if re.search(r"^(def |class |import |from |async def )", sample, re.MULTILINE):
        return ChunkStrategy.CODE
    if "```" in text and re.search(r"^#{1,6}\s+", sample, re.MULTILINE):
        return ChunkStrategy.MARKDOWN
    if re.search(r"^(User|Human|Assistant|AI):", sample, re.MULTILINE):
        return ChunkStrategy.CONVERSATION
    if re.search(r"^#{1,6}\s+", sample, re.MULTILINE):
        return ChunkStrategy.MARKDOWN
    if "\n\n" in text:
        return ChunkStrategy.PARAGRAPH
    if re.search(r"[.!?]\s+[A-Z]", sample):
        return ChunkStrategy.SENTENCE
    return ChunkStrategy.CHARACTER


# ── Main chunker class ───────────────────────────────────────────────────


class SemanticChunker:
    """Pluggable text chunker with multiple strategies.

    Strategies are exposed as methods; the main entry point is
    :meth:`chunk` which dispatches based on the requested strategy
    (or auto-detection).

    Attributes:
        target_size: Target chunk size in characters. Default 1024.
        overlap: Overlap between consecutive chunks in characters.
        default_strategy: Strategy to use when none is specified.
    """

    def __init__(
        self,
        target_size: int = 1024,
        overlap: int = 128,
        default_strategy: ChunkStrategy = ChunkStrategy.AUTO,
    ) -> None:
        """Initialise the chunker.

        Args:
            target_size: Target chunk size in characters.
            overlap: Overlap between consecutive chunks in characters.
                Must be in ``[0, target_size)``.
            default_strategy: Default strategy for :meth:`chunk` when
                the caller doesn't specify one.

        Raises:
            ValueError: If ``overlap`` is invalid for ``target_size``.
        """
        if target_size <= 0:
            raise ValueError(f"target_size must be > 0, got {target_size}")
        if overlap < 0 or overlap >= target_size:
            raise ValueError(
                f"overlap must be in [0, target_size), got {overlap} vs {target_size}"
            )
        self.target_size = target_size
        self.overlap = overlap
        self.default_strategy = default_strategy
        self._strategies: dict[ChunkStrategy, Callable[..., list[Chunk]]] = {
            ChunkStrategy.CHARACTER: _char_chunks,
            ChunkStrategy.SENTENCE: _sentence_chunks,
            ChunkStrategy.PARAGRAPH: _paragraph_chunks,
            ChunkStrategy.CODE: _code_chunks,
            ChunkStrategy.MARKDOWN: _markdown_chunks,
            ChunkStrategy.CONVERSATION: _conversation_chunks,
        }

    def chunk(
        self,
        text: str,
        strategy: ChunkStrategy | None = None,
        source_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Split ``text`` into chunks using the chosen strategy.

        Args:
            text: Source text. May be empty (returns empty list).
            strategy: Strategy to use. ``None`` falls back to
                :attr:`default_strategy`. ``ChunkStrategy.AUTO`` triggers
                heuristic detection.
            source_id: Optional source identifier embedded in chunk
                metadata (useful for traceability in vector stores).
            extra_metadata: Optional metadata dict merged into every
                chunk's metadata.

        Returns:
            A list of :class:`Chunk` objects.
        """
        if not text:
            return []
        chosen = strategy or self.default_strategy
        if chosen == ChunkStrategy.AUTO:
            chosen = _detect_strategy(text)
        impl = self._strategies.get(chosen)
        if impl is None:
            raise ValueError(f"Unknown strategy: {chosen!r}")

        chunks = impl(text, self.target_size, self.overlap)

        # Attach shared metadata
        for c in chunks:
            c.metadata.setdefault("source_id", source_id)
            c.metadata.setdefault("chunk_uuid", str(uuid.uuid4()))
            if extra_metadata:
                for k, v in extra_metadata.items():
                    c.metadata.setdefault(k, v)

        return chunks

    def chunk_batch(
        self,
        texts: list[str],
        strategy: ChunkStrategy | None = None,
    ) -> list[list[Chunk]]:
        """Apply :meth:`chunk` to a batch of texts."""
        return [self.chunk(t, strategy=strategy) for t in texts]

    def stats(self, chunks: list[Chunk]) -> dict[str, Any]:
        """Compute statistics over a list of chunks."""
        if not chunks:
            return {
                "count": 0,
                "total_chars": 0,
                "total_tokens_est": 0,
                "avg_chunk_chars": 0,
                "strategies": {},
            }
        strategies: dict[str, int] = {}
        total_chars = 0
        total_tokens = 0
        for c in chunks:
            strategies[c.strategy] = strategies.get(c.strategy, 0) + 1
            total_chars += len(c.text)
            total_tokens += c.token_estimate
        return {
            "count": len(chunks),
            "total_chars": total_chars,
            "total_tokens_est": total_tokens,
            "avg_chunk_chars": total_chars // len(chunks),
            "strategies": strategies,
        }


# ── Public convenience functions ─────────────────────────────────────────


def chunk_text(
    text: str,
    target_size: int = 1024,
    overlap: int = 128,
    strategy: ChunkStrategy = ChunkStrategy.AUTO,
) -> list[Chunk]:
    """Convenience: chunk ``text`` with a one-shot SemanticChunker.

    Args:
        text: Source text.
        target_size: Target chunk size in characters.
        overlap: Overlap in characters (must be < ``target_size``).
        strategy: Strategy to use. ``AUTO`` triggers detection.

    Returns:
        A list of :class:`Chunk` objects.
    """
    chunker = SemanticChunker(
        target_size=target_size, overlap=overlap, default_strategy=strategy
    )
    return chunker.chunk(text, strategy=strategy)
