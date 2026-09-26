"""One statement, shortened to a line a reader can recognise it by.

Four places needed this and each did it differently. The report's pending list
took `text.splitlines()[0]`, which for any script carrying a comment header is
a comment: measured against 129 real scripts it printed `--` for one file and
the bare word `SELECT` for another, and a reader looking for which of their
queries was declined was shown neither. The three `dropped ...` Decisions in
tier 1 took `splitlines()[-1][:60]`, right for the one-line `SET` it was
written for and wrong for anything that wraps.

## Why sqlglot and not a scanner

The first version of this module answered it by hand: skip comment lines, cut
a trailing `--` with a quote-aware scan, join what is left. Review broke it
seven ways in one sitting, and every break was one of the two failures the
module exists to prevent. It hid SQL the reader wrote -- a backslash-escaped
quote (`'it\\'s'`), a string literal spanning two lines, a dollar-quoted body
(`$$ ... $$`), a bracketed identifier (`[my--table]`) each ended the line
early, with no ellipsis to say anything was missing. And it showed comment
text as if it were code -- a mid-line `/* b */`, a nested `/* /* */ */`, a
trailing MySQL `#` all leaked through, the last of them reading exactly as
though it commented out the `FROM` joined after it.

None of those are exotic. They are what a comment-and-quote scanner costs,
and this codebase already depends on a parser that knows every one of these
rules per dialect. So the statement is parsed and re-rendered without its
comments, and the only hand-written path is the one for text that is not SQL
at all -- where it picks a line to show and claims nothing about its contents.

## What it shows

The statement as sqlglot reads it, on one line, cut at `LINE_LIMIT`. Not
byte-for-byte what the reader typed: whitespace, quoting and keywords come
back normalised, so `DECLARE @d INT` is shown as `DECLARE @d INTEGER`. That is
the cost of not hand-writing the comment removal, and it is worth paying --
it is also the same rendering their converted model bodies get, so the two
agree, and a reader reading this report is reading normalised SQL either way.

A display convenience and never a claim about meaning --
nothing downstream reads what this returns, and anything that needs to know
what a statement *does* parses it itself.
"""

from __future__ import annotations

import sqlglot
from sqlglot.errors import SqlglotError

# Long enough for a recognisable projection and its FROM, short enough that a
# list of them stays a list rather than a wall.
LINE_LIMIT = 88

NO_SQL = "no SQL in this statement, only comments"

_COMMENT_STARTS = ("--", "#", "/*", "*")


def _cut(line: str, limit: int) -> str:
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"


def _first_uncommented_line(text: str) -> str:
    """The first line of `text` that does not look like a comment, as written.

    The path for text that is not SQL a parser will take -- the `\\! echo ...`
    a MySQL script hands its client, a statement truncated mid-literal. It
    picks which line to show and says nothing about what is in it: no comment
    is stripped from inside a line and no lines are joined, because both of
    those are claims, and making them without a parse is what the version this
    replaced got wrong.
    """
    inside_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if inside_block:
            if "*/" not in line:
                continue
            inside_block = False
            line = line.split("*/", 1)[1].strip()
        # A `/*` with no `*/` after it takes the rest of the statement with
        # it, which is the only way a line holding no comment marker of its
        # own is still inside a comment. Erring toward "this is a comment" is
        # the safe direction here: the cost is showing a later line, or none,
        # where showing comment text as though it were SQL is the failure
        # this module exists to prevent.
        head, opener, tail = line.partition("/*")
        if opener and "*/" not in tail:
            inside_block = True
            line = head.strip()
        if line and not line.startswith(_COMMENT_STARTS):
            return line
    return ""


def excerpt(text: str, dialect: str | None = None, limit: int = LINE_LIMIT) -> str:
    """`text` as one line a reader can recognise the statement by.

    A statement that is only comments says so rather than showing one: echoing
    the comment is the whole mistake this module exists to stop, and a blank
    where a statement should be reads as a bug.
    """
    try:
        node = sqlglot.parse_one(text, read=dialect)
    except SqlglotError:
        node = None
    if node is not None:
        # `comments=False` is sqlglot's own comment removal, which knows where
        # a `--` is a comment and where it is four characters of a string.
        # Whitespace is collapsed after it, because a literal spanning two
        # lines comes back spanning two lines and this has to be one.
        rendered = " ".join(node.sql(dialect=dialect, comments=False).split())
        if rendered:
            return _cut(rendered, limit)
    line = _first_uncommented_line(text)
    return _cut(line, limit) if line else NO_SQL
