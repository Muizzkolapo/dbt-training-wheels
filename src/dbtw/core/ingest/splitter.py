"""Splits raw SQL text into statement spans.

Tokenizer-based: semicolons inside strings never split (they are STRING
tokens), and BEGIN/CASE...END depth tracking keeps procedure bodies whole.
Comments occupy the raw text between statements, so slicing between
semicolon offsets attaches leading comments to the following statement.

A piece with no token is not a statement, and it is not emitted. The
tokenizer gives a comment no token, so such a piece is only comments: the
text after the last statement, a file of only comments, or the text before
a `;` that closes nothing. Emitted, it was classified as a statement that
could not be parsed, and it showed as pending work.

A span's text keeps those leading comments, because they become the leading
comments of the model. Its `line_start` is the line of its first SQL token,
because that is the line a reader looks for: a Decision that cites the
comment above an INSERT points at the wrong line. The line of the first
character of the text is `line_end - text.count("\n")`.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import TokenType
from sqlglot.errors import SqlglotError

_BEGIN_TRANSACTION_WORDS = frozenset({"TRANSACTION", "TRAN", "WORK"})


@dataclass(frozen=True, slots=True)
class Span:
    text: str
    line_start: int  # 1-based: the first SQL line, after any leading comment
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
    # The offset of the first token after `start`. A comment is not a token,
    # so this is where the SQL of the statement starts.
    first: int | None = None
    for i, tok in enumerate(tokens):
        if first is None and tok.token_type != TokenType.SEMICOLON:
            first = tok.start
        if tok.token_type == TokenType.BEGIN:
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            is_transaction_begin = (
                nxt is None
                or nxt.token_type == TokenType.SEMICOLON
                or nxt.text.upper() in _BEGIN_TRANSACTION_WORDS
            )
            if not is_transaction_begin:
                depth += 1
        elif tok.token_type == TokenType.CASE:
            depth += 1
        elif tok.token_type == TokenType.END:
            depth = max(0, depth - 1)
        elif tok.token_type == TokenType.SEMICOLON and depth == 0:
            if first is not None:
                span = _span_between(text, start, tok.start, first)
                if span:
                    spans.append(span)
            start = tok.end + 1
            first = None
    if first is not None:
        tail = _span_between(text, start, len(text), first)
        if tail:
            spans.append(tail)
    return spans


def _span_between(text: str, begin: int, end: int, first: int | None = None) -> Span | None:
    """The statement in `text[begin:end]`, or None when it is only whitespace.

    `first` is the offset of its first SQL token. It is None only for input
    that could not be tokenized: `split_sql` emits no piece that tokenized to
    nothing. Then the statement starts where its text starts.
    """
    piece = text[begin:end]
    stripped = piece.strip()
    if not stripped:
        return None
    lead = len(piece) - len(piece.lstrip())
    abs_start = begin + lead
    abs_end = abs_start + len(stripped)  # exclusive
    sql_start = abs_start if first is None else first
    return Span(
        text=stripped,
        line_start=text.count("\n", 0, sql_start) + 1,
        line_end=text.count("\n", 0, abs_end - 1) + 1,
    )
