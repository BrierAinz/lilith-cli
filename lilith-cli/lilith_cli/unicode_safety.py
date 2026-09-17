"""Unicode normalization at conversation and JSON boundaries."""

from __future__ import annotations

from typing import Any


def sanitize_text(text: str) -> str:
    """Replace unpaired UTF-16 surrogates while preserving valid pairs.

    Python strings can contain lone code points in U+D800..U+DFFF, but those
    values cannot be encoded as UTF-8.  A UTF-16 round trip with
    ``surrogatepass`` preserves legitimate pairs and turns only malformed
    sequences into the Unicode replacement character.
    """
    if not any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        return text
    return text.encode("utf-16", errors="surrogatepass").decode(
        "utf-16", errors="replace"
    )


def sanitize_unicode(value: Any) -> Any:
    """Return a JSON-like value with unsafe surrogates removed recursively."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {
            sanitize_text(key) if isinstance(key, str) else key: sanitize_unicode(
                item
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_unicode(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_unicode(item) for item in value)
    return value
