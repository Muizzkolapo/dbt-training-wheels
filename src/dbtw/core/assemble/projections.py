"""What columns a query body puts out, read off the body itself. No I/O."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


def known_projections(
    body: str, dialect: str | None
) -> tuple[list[tuple[str, bool]], bool, bool] | None:
    """The named, non-star output columns a query body projects, as
    (name, was-written-quoted) pairs, plus whether a star projection (`*`
    or `t.*`) is present anywhere in it, plus whether any projection has no
    output name at all.

    The three are kept apart rather than folded together because they are
    three different unknowns, and a caller that conflates them claims a
    construct that was never there (FINDING 6). The two flags agree on one
    thing only: either being true means the list of names is not the whole
    output row.

    Works on any `exp.Query` -- a plain `SELECT` or a set operation
    (`UNION`/`INTERSECT`/`EXCEPT`) alike, via `.selects`, which sqlglot's
    own `named_selects` is built on. An unaliased compound projection (a
    bare `CASE` with no `AS`) is left out of the list and reported by the
    second flag. `assembler._key_status` reads the list to settle a
    --unique-key column and can ignore that flag: a projection with no
    output name can never match a key, so leaving it out never hides a real
    match. `emit.example` reads the list for the opposite purpose -- to name
    every column of a row -- and must honour the flag, since a list missing
    one of the row's columns describes a narrower table than the model
    builds.

    None means the body couldn't be parsed as a query at all -- should not
    happen for an append draft's body (always exactly the INSERT's own
    SELECT, re-parsed with the same dialect it was rendered with), but
    callers must describe this honestly rather than folding it into the
    star case, which would claim a construct that was never actually
    there (FINDING 6). This function reads SQL and only SQL: a body that
    has already been rewritten into dbt Jinja does not parse, and a caller
    holding one must say so rather than pass it in and read the None as a
    fact about the query.
    """
    try:
        node = sqlglot.parse_one(body, read=dialect)
    except SqlglotError:
        return None
    if not isinstance(node, exp.Query):
        return None

    projections: list[tuple[str, bool]] = []
    has_star = False
    has_unnamed = False
    for projection in node.selects:
        name = projection.alias_or_name
        if not name:
            has_unnamed = True  # unnamed (e.g. a bare CASE): can never match a key
            continue
        if name == "*":
            has_star = True
            continue
        if isinstance(projection, exp.Alias):
            identifier = projection.args.get("alias")
        elif isinstance(projection, exp.Column):
            identifier = projection.this
        else:
            identifier = None
        quoted = bool(isinstance(identifier, exp.Identifier) and identifier.quoted)
        projections.append((name, quoted))
    return projections, has_star, has_unnamed
