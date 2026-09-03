"""Splits raw SQL text into statement spans.

Tokenizer-based: semicolons inside strings never split (they are STRING
tokens), and BEGIN/CASE...END depth tracking keeps procedure bodies whole.
Comments occupy the raw text between statements, so slicing between
semicolon offsets attaches leading comments to the following statement.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import TokenType
from sqlglot.errors import SqlglotError

_OPENERS = frozenset({TokenType.BEGIN, TokenType.CASE})


@dataclass(frozen=True, slots=True)
class Span:
    text: str
    line_start: int  # 1-based, inclusive
    line_end: int  # 1-based, inclusive


def split_sql(text: str, dialect: str | None = None) -> list[Span]:
    try:
        tokens = sqlglot.tokenize(text, read=dialect)
    except SqlglotError:
        # Untokenizable input: one span; classification will surface the error.
        span = _span_between(text, 0, len(text))
        return [span] if span else []

    spans: list[Span] = []
    depth = 0
    start = 0
    for tok in tokens:
        if tok.token_type in _OPENERS:
            depth += 1
        elif tok.token_type == TokenType.END:
            depth = max(0, depth - 1)
        elif tok.token_type == TokenType.SEMICOLON and depth == 0:
            span = _span_between(text, start, tok.start)
            if span:
                spans.append(span)
            start = tok.end + 1
    tail = _span_between(text, start, len(text))
    if tail:
        spans.append(tail)
    return spans


def _span_between(text: str, begin: int, end: int) -> Span | None:
    piece = text[begin:end]
    stripped = piece.strip()
    if not stripped:
        return None
    lead = len(piece) - len(piece.lstrip())
    abs_start = begin + lead
    abs_end = abs_start + len(stripped)  # exclusive
    return Span(
        text=stripped,
        line_start=text.count("\n", 0, abs_start) + 1,
        line_end=text.count("\n", 0, abs_end - 1) + 1,
    )
