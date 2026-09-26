"""A table this script writes is never proposed as one it reads.

A source declaration says "this is a raw table, external to the project,
that we read from". A table the reader's own script writes into is not that,
whatever happened to the statement that writes it -- and saying otherwise
puts a claim about their warehouse in a file they commit.

This is reachable because deferral is honest. A statement this converter
cannot convert yet stays pending, so nothing in the change builds its target;
and a *later* statement that reads that target then has a reference matching
no draft and no existing model, which is exactly the shape of an external
one. The fix is not to convert more -- it is to stop calling a written table
external.
"""

from __future__ import annotations

from tests.unit.assemble.helpers import convert

# The INSERT has a column list and no TRUNCATE to pair with, so it is
# deferred (catalog 2.8). Nothing here builds finance.revenue_daily -- and
# the view below reads it.
WRITES_THEN_READS = (
    "INSERT INTO finance.revenue_daily (day, total)\n"
    "SELECT order_date, SUM(amount) FROM raw.orders GROUP BY order_date;\n"
    "CREATE VIEW finance.v_latest AS\n"
    "SELECT d.day, d.total, c.name\n"
    "FROM finance.revenue_daily AS d JOIN raw.customers AS c ON c.day = d.day;\n"
)


def _sources(change) -> set[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return {(entry.source_name, entry.table) for entry in change.sources}


def test_a_deferred_statements_target_is_not_declared_as_a_source() -> None:
    change = convert(WRITES_THEN_READS)

    assert change.pending, "this script should leave the INSERT pending"
    assert ("finance", "revenue_daily") not in _sources(change), (
        "a table this script writes was declared as a raw source it reads"
    )


def test_the_table_that_really_is_external_is_still_declared() -> None:
    """The fix must not take the honest source declarations with it:
    raw.customers is read by a model this change builds and is written by
    nothing, which is exactly what a source is.
    """
    change = convert(WRITES_THEN_READS)

    assert ("raw", "customers") in _sources(change)


def test_the_reference_to_it_is_left_alone_rather_than_pointed_somewhere_wrong() -> None:
    """With no model and no source for it, the reference has nowhere correct
    to go -- so it stays as the reader wrote it. A `source()` call would be a
    lie, and a `ref()` would name a model this change does not build.
    """
    change = convert(WRITES_THEN_READS)

    (view,) = [m for m in change.models if m.name.endswith("v_latest")]
    assert "source('finance', 'revenue_daily')" not in view.body
    assert "ref('revenue_daily')" not in view.body


def test_the_reader_is_told_why_it_is_neither() -> None:
    """Nothing is silent. A table that is neither a model here nor a source
    is a gap the reader has to close, so the conversion says so rather than
    leaving them to notice.
    """
    change = convert(WRITES_THEN_READS)

    said = " ".join(d.action + " " + d.reason for d in change.decisions)
    assert "revenue_daily" in said


def test_a_table_an_update_touches_is_not_a_source_either() -> None:
    """A table whose rows a script changes is a table that script owns.

    `_writes_to` is deliberately wider than "what does this statement build":
    an UPDATE builds nothing and still makes the table the reader's, so
    calling it a raw source read from elsewhere is the same mistake as it
    would be for the INSERT above.
    """
    change = convert(
        "UPDATE finance.ledger SET posted = 1 WHERE posted IS NULL;\n"
        "CREATE VIEW finance.v_posted AS SELECT * FROM finance.ledger;\n"
    )

    assert change.pending, "an UPDATE has no model to become, so it stays pending"
    assert ("finance", "ledger") not in _sources(change)


def test_a_table_a_delete_touches_is_not_a_source_either() -> None:
    change = convert(
        "DELETE FROM finance.ledger WHERE posted IS NULL;\n"
        "CREATE VIEW finance.v_posted AS SELECT * FROM finance.ledger;\n"
    )

    assert change.pending
    assert ("finance", "ledger") not in _sources(change)


def test_a_statement_that_cannot_be_parsed_takes_nothing_with_it() -> None:
    """A statement this converter cannot parse is one it knows nothing about,
    including what it writes.

    Skipped rather than guessed at, and the honest cost is stated: a table
    written only by an unparseable statement can still be proposed as a
    source. That is a declaration the reader corrects, which is better than
    one they cannot see is wrong.
    """
    change = convert(
        "THIS IS NOT SQL AT ALL(((;\nCREATE VIEW finance.v_x AS SELECT * FROM raw.customers;\n"
    )

    assert ("raw", "customers") in _sources(change), "the honest source survived"
