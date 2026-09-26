"""One statement, shortened to a line a reader can recognise it by.

Four places needed this and each did it differently. The report's pending list
took `text.splitlines()[0]`, which for any script carrying a comment header is
a comment: measured against 129 real scripts it printed `--` for one file and
the bare word `SELECT` for another, and a reader looking for which of their
queries was declined was shown neither. The three `dropped ...` Decisions in
tier 1 took `splitlines()[-1][:60]`, which is right for the one-line `SET` it
was written for and wrong for anything that wraps.

Taking the first line of real SQL fixes the header and still fails the second
case, because plenty of people write `SELECT` on a line of its own -- so the
statement is collapsed onto one line and then cut. What a reader recognises is
the projection and the table, and those are the first thing they wrote.

This is a display convenience and never a claim about meaning. The comment
scan is quote-aware but it is not a parser, and nothing downstream reads what
this returns: anything that needs to know what a statement *does* parses it.
"""

from __future__ import annotations

# Long enough for a recognisable projection and its FROM, short enough that a
# list of them stays a list rather than a wall.
LINE_LIMIT = 88

NO_SQL = "no SQL in this statement, only comments"

_QUOTES = "'\"`"


def _without_trailing_comment(line: str) -> str:
    """`line` up to a `--` that starts a comment, quotes respected.

    Worth the scan because lines are joined: leaving `SELECT a -- the good
    ones` intact and joining `FROM t` onto it produces a line that reads as
    though the FROM were commented out, which is worse than showing nothing.
    """
    quote = ""
    for position, character in enumerate(line):
        if quote:
            if character == quote:
                quote = ""
        elif character in _QUOTES:
            quote = character
        elif line.startswith("--", position):
            return line[:position].rstrip()
    return line


def sql_only(text: str) -> str:
    """`text` with its comments removed and what is left on one line.

    Line-based rather than a regex over the whole statement: a `/* */` matched
    by pattern would also match one inside a string literal, and the point is
    to show a reader the SQL they wrote.
    """
    kept: list[str] = []
    inside_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if inside_block:
            if "*/" not in line:
                continue
            inside_block = False
            line = line.split("*/", 1)[1].strip()
        while line.startswith("/*"):
            if "*/" in line:
                line = line.split("*/", 1)[1].strip()
                continue
            inside_block = True
            line = ""
        # `#` only at the start of a line, and deliberately not in
        # `_without_trailing_comment`: MySQL spells a comment that way, but
        # T-SQL spells a temporary table `#temp`, and this function has no
        # dialect to tell them apart. A line that *begins* `#` is a comment in
        # the one and not valid SQL in the other, so the start of a line is
        # the only place the two cannot be confused.
        #
        # There is no `--` test here on purpose: `_without_trailing_comment`
        # empties a line that is entirely a comment, and a check restating
        # that would be a guard that guards nothing. A mutation removing it
        # changed no behaviour, which is how it was found.
        if not line or line.startswith("#"):
            continue
        line = _without_trailing_comment(line)
        if line:
            kept.append(line)
    return " ".join(kept)


def excerpt(text: str, limit: int = LINE_LIMIT) -> str:
    """`sql_only`, cut to `limit` with an ellipsis, or `NO_SQL`.

    A statement that is only comments says so rather than showing one: echoing
    the comment is the whole mistake this module exists to stop, and a blank
    where a statement should be reads as a bug.
    """
    line = sql_only(text)
    if not line:
        return NO_SQL
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"
