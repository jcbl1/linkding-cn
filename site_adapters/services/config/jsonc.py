"""
Unified JSONC utilities for reading and writing JSON with Comments.

Design:
  - parse(text) → dict: strip comments/trailing commas, delegate to json.loads
  - update_key(text, key, value) → str: replace a top-level key's value,
    preserving surrounding comments and formatting
  - extract_comments(text) → dict[str, str]: map top-level keys to their
    preceding comments (for potential re-insertion elsewhere)

Approach inspired by n-takumasa/json-with-comments: comments are keyed by
position (which key they precede), not by absolute offset.
"""

from __future__ import annotations

import json
import re
from typing import Any


# ---------------------------------------------------------------------------
# Parsing: JSONC text → Python object
# ---------------------------------------------------------------------------

# Regex to strip C-style comments (preserving strings).
# Same approach as n-takumasa/json-with-comments/_util.py.
_STRIP_COMMENT_RE = re.compile(
    r'("(?:\\.|[^\\"])*")'   # group 1: string literal
    r'|'
    r'(/\*.*?\*/|//[^\r\n]*)',  # group 2: comment
    re.DOTALL,
)

_STRIP_TRAILING_COMMA_RE = re.compile(
    r'("(?:\\.|[^\\"])*")'   # group 1: string literal
    r'|'
    r',(\s*[}\]])',           # group 2: closing brace/bracket after comma
    re.DOTALL,
)


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments, preserving string literals."""
    return _STRIP_COMMENT_RE.sub(lambda m: m.group(1) or '', text)


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ]."""
    return _STRIP_TRAILING_COMMA_RE.sub(lambda m: m.group(1) or m.group(2), text)


def parse(text: str) -> Any:
    """Parse JSONC text to a Python object.

    Handles // comments, /* */ comments, and trailing commas.
    Raises json.JSONDecodeError on invalid JSON after stripping.
    """
    cleaned = _strip_trailing_commas(_strip_comments(text))
    return json.loads(cleaned)


# ---------------------------------------------------------------------------
# String/token scanning helpers (for comment-preserving editing)
# ---------------------------------------------------------------------------

def _read_string(text: str, start: int) -> tuple[str, int]:
    """Read a quoted string starting at *start*. Returns (content, end_pos)."""
    quote = text[start]
    parts: list[str] = []
    i = start + 1
    escaped = False
    while i < len(text):
        ch = text[i]
        if escaped:
            parts.append(ch)
            escaped = False
        elif ch == '\\':
            escaped = True
        elif ch == quote:
            return ''.join(parts), i + 1
        else:
            parts.append(ch)
        i += 1
    raise ValueError('unterminated string')


def _skip_ws(text: str, start: int) -> int:
    """Skip whitespace and comments starting at *start*."""
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == '/' and i + 1 < n and text[i + 1] == '/':
            i += 2
            while i < n and text[i] not in '\r\n':
                i += 1
        elif ch == '/' and i + 1 < n and text[i + 1] == '*':
            i += 2
            while i + 1 < n and text[i:i + 2] != '*/':
                i += 1
            i += 2
        else:
            break
    return i


def _find_value_end(text: str, start: int) -> int:
    """Find the end position of a JSONC value starting at *start*.

    Returns the index *after* the last character of the value.
    Handles nested objects/arrays, strings, and comments.
    """
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'"):
            _, i = _read_string(text, i)
            continue
        if text[i:i + 2] == '//':
            i += 2
            while i < n and text[i] not in '\r\n':
                i += 1
            continue
        if text[i:i + 2] == '/*':
            i += 2
            while i + 1 < n and text[i:i + 2] != '*/':
                i += 1
            i += 2
            continue
        if ch in '[{':
            depth += 1
        elif ch in ']}':
            if depth == 0:
                return i
            depth -= 1
        elif ch == ',' and depth == 0:
            return i
        i += 1
    return n


def _find_top_level_key_span(text: str, key: str) -> tuple[int, int] | None:
    """Find the value span (start, end) for a top-level key.

    Returns (value_start, value_end) or None if key not found.
    The span covers the raw text of the value (including nested comments).
    """
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'"):
            token_end = _read_string(text, i)[1]
            token = text[i + 1:token_end - 1]  # extract string content
            j = _skip_ws(text, token_end)
            if depth == 1 and token == key and j < n and text[j] == ':':
                value_start = _skip_ws(text, j + 1)
                value_end = _find_value_end(text, value_start)
                return value_start, value_end
            i = token_end
            continue
        if text[i:i + 2] == '//':
            i += 2
            while i < n and text[i] not in '\r\n':
                i += 1
            continue
        if text[i:i + 2] == '/*':
            i += 2
            while i + 1 < n and text[i:i + 2] != '*/':
                i += 1
            i += 2
            continue
        if ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
        i += 1
    return None


def _line_indent(text: str, pos: int) -> str:
    """Return the whitespace indent of the line containing *pos*."""
    line_start = text.rfind('\n', 0, pos) + 1
    i = line_start
    while i < len(text) and text[i] in ' \t':
        i += 1
    return text[line_start:i]


def _render_value(value: Any, indent: str = '  ') -> str:
    """Render a Python value as JSON, adjusting indentation to match context."""
    rendered = json.dumps(value, indent=2, ensure_ascii=False)
    return rendered.replace('\n', '\n' + indent)


# ---------------------------------------------------------------------------
# Comment-preserving editing
# ---------------------------------------------------------------------------

def update_key(text: str, key: str, value: Any) -> str:
    """Replace a top-level key's value in JSONC text, preserving comments.

    If the key exists, its value is replaced in-place.
    If the key doesn't exist, it is appended inside the top-level object.
    If the text is empty, a new object is created.

    This is the safe alternative to views.py's _replace_top_level_jsonc_value.
    """
    rendered = json.dumps(value, indent=2, ensure_ascii=False)

    span = _find_top_level_key_span(text, key)
    if span:
        start, end = span
        indent = _line_indent(text, start) + '  '
        return text[:start] + rendered.replace('\n', '\n' + indent) + text[end:]

    # Key doesn't exist — insert it.
    stripped = text.strip()
    if not stripped:
        return '{\n  "' + key + '": ' + _render_value(value) + '\n}\n'

    open_idx = text.find('{')
    if open_idx < 0:
        raise ValueError('global.jsonc must be an object')

    content_start = _skip_ws(text, open_idx + 1)
    has_existing = content_start < len(text) and text[content_start] != '}'
    addition = '\n  "' + key + '": ' + _render_value(value)
    if has_existing:
        addition += ','
    return text[:open_idx + 1] + addition + text[open_idx + 1:]


# ---------------------------------------------------------------------------
# Comment extraction (for future use: migrate comments between formats)
# ---------------------------------------------------------------------------

def extract_comments(text: str) -> dict[str, str]:
    """Extract comments that precede top-level keys.

    Returns {key: comment_text} for each key that has a preceding comment.
    Comment text includes the // or /* */ delimiters.
    """
    comments: dict[str, str] = {}
    depth = 0
    i = 0
    n = len(text)
    pending_comment = ''

    while i < n:
        ch = text[i]

        # Collect comments at depth 1 (inside top-level object)
        if depth == 1:
            if text[i:i + 2] == '//':
                comment_start = i
                i += 2
                while i < n and text[i] not in '\r\n':
                    i += 1
                pending_comment += text[comment_start:i]
                continue
            if text[i:i + 2] == '/*':
                comment_start = i
                i += 2
                while i + 1 < n and text[i:i + 2] != '*/':
                    i += 1
                i += 2
                pending_comment += text[comment_start:i]
                continue

        # When we hit a string at depth 1, it might be a key
        if ch in ('"', "'") and depth == 1:
            token, end = _read_string(text, i)
            j = _skip_ws(text, end)
            if j < n and text[j] == ':':
                if pending_comment.strip():
                    comments[token] = pending_comment.strip()
                pending_comment = ''
            i = end
            continue

        if ch in '[{':
            depth += 1
            if depth == 1:
                pending_comment = ''
        elif ch in ']}':
            depth -= 1

        if depth < 1:
            pending_comment = ''

        i += 1

    return comments
