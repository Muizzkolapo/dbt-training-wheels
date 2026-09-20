"""Your SQL beside the model it became.

The screen that answers the one question a reader can check without knowing
any dbt: is this still my query? Everything else in the walk explains what was
decided. This shows what was done, in their text and in dbt's.
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

import pytest
from tests.unit.web.conftest import Walk
from tests.unit.web.helpers import CHAINED_INSERTS, UNPARSEABLE_AND_APPEND
from tests.unit.web.page import normalised, read

from dbtw.core.emit.render import render_model
from dbtw.web.app import MissingOriginalError, _conversions


def _rows(body: str) -> dict[str, str]:
    """The page's per-model rows, by model name.

    `page.py` records runs and markers, never which run sits inside which
    element, so "this model is beside this SQL" is a question it cannot
    answer -- and that question is the entire reason this screen exists. Three
    one-line mutations that showed every model beside another model's SQL
    once passed the whole suite, because every assertion here asked only
    whether a string was *somewhere* on the page. Splitting the body on the
    row boundary is what makes adjacency assertable.
    """
    chunks = body.split('<li data-item="model">')[1:]
    rows: dict[str, str] = {}
    for chunk in chunks:
        row = chunk.split("</li>")[0]
        name = re.search(r"<h2><span data-engine[^>]*>([^<]+)</span>", row)
        assert name is not None, f"a row on this screen names no model: {row[:120]}"
        rows[name.group(1)] = row
    return rows


def _blocks(row: str) -> list[str]:
    """Every `<pre>` in one row, in the order the page has them."""
    return [unescape(block.split("</pre>")[0].split(">", 1)[1]) for block in row.split("<pre")[1:]]


def test_each_model_is_shown_beside_its_own_sql_and_nobody_else_s(
    walk: Walk, walk_sql: Path
) -> None:
    """The claim this screen is for, asserted per row.

    Both halves matter. Each row holds exactly the statements that built that
    model, in the order the file had them -- and holds no other model's
    statement, which is the half a page-wide `in` check can never make.
    """
    app, client, session = walk(walk_sql)
    view = session.view()
    by_name = {model.name: model for model in view.change.models}
    assert len(by_name) > 1, "this conversion should build more than one model"

    rows = _rows(client.get("/changed").get_data(as_text=True))

    assert set(rows) == set(by_name)
    for name, row in rows.items():
        model = by_name[name]
        mine = [view.statements[i].raw.text for i in sorted(model.source_indices)]
        theirs = [
            view.statements[i].raw.text
            for other in view.change.models
            if other.name != name
            for i in other.source_indices
        ]
        blocks = _blocks(row)
        assert blocks[: len(mine)] == mine, f"{name} is shown beside the wrong statements"
        assert blocks[len(mine)] == render_model(model), f"{name} is shown the wrong model file"
        for text in theirs:
            if text in mine:
                continue
            assert text not in row, f"{name} is shown beside another model's SQL"


def test_the_statements_in_a_row_are_in_the_order_the_file_had_them(
    walk: Walk, walk_sql: Path
) -> None:
    """A TRUNCATE and the INSERT that refills it read as a pair in one order
    and as nonsense in the other. `Conversion` promises the file's order, and
    reversing it is a one-line change nothing else here would notice.
    """
    app, client, session = walk(walk_sql)
    view = session.view()
    folded = next(m for m in view.change.models if len(m.source_indices) > 1)

    row = _rows(client.get("/changed").get_data(as_text=True))[folded.name]

    assert _blocks(row)[: len(folded.source_indices)] == [
        view.statements[i].raw.text for i in sorted(folded.source_indices)
    ]


def test_the_after_is_the_text_the_files_screen_shows_for_that_model(
    walk: Walk, walk_sql: Path
) -> None:
    """One renderer, held against the other screen rather than against the
    function both call.

    Asserting `render_model(model) in shown` was close to a tautology: the
    page renders exactly that, so the assertion restated the implementation.
    Appending a line inside `emit` left it passing while the two screens
    showed different text for one model. Read off the files screen, it
    cannot.
    """
    app, client, session = walk(walk_sql)
    rows = _rows(client.get("/changed").get_data(as_text=True))
    files = read(client.get("/files").get_data(as_text=True))
    previewed = {normalised(run) for run in files.engine}

    for name, row in rows.items():
        after = _blocks(row)[-1]
        assert normalised(after) in previewed, f"{name}'s after is on no file the walk will write"


def test_a_statement_that_became_no_model_is_not_shown_here(walk: Walk, sql_script) -> None:
    """There is no after to put beside it.

    A statement this conversion could not convert -- here, one sqlglot cannot
    parse -- belongs on the caveats screen, which says what happened to it.
    On this screen it would be a before with an empty column beside it, which
    reads as a model that came out blank.
    """
    app, client, session = walk(sql_script(UNPARSEABLE_AND_APPEND))
    change = session.view().change
    assert change.pending, "this script should leave a statement pending"

    page = read(client.get("/changed").get_data(as_text=True))

    shown = {normalised(run) for run in page.engine}
    for _, statement in change.pending:
        assert normalised(statement.raw.text) not in shown


def test_a_model_naming_a_statement_this_conversion_did_not_read_is_refused(
    walk: Walk, walk_sql: Path
) -> None:
    """The join is on an index, and an index that does not land is a screen
    putting one model beside another model's SQL -- the most convincing wrong
    thing this walk could show. Refused rather than skipped.
    """
    app, client, session = walk(walk_sql)
    change = session.view().change

    with pytest.raises(MissingOriginalError):
        _conversions(change, session.originals()[:1])


def test_the_pairing_holds_when_the_walk_was_given_a_dialect(walk: Walk, sql_script) -> None:
    """The dialect decides where statements split, so it decides the index
    space this whole screen joins on.

    `split_sql` tokenizes with `read=dialect`, and every index a model names
    is a position in the list that split produced. A walk given a dialect was
    in no test that rendered this screen, which is the coverage-set shape
    this file has been fixed for before: the join was only ever checked in
    the one state where the parameter could not matter.
    """
    tsql = (
        "DECLARE @cutoff DATE = '2024-01-01';\n"
        "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders "
        "WHERE order_date >= @cutoff;\n"
    )
    app, client, session = walk(sql_script(tsql), dialect="tsql")
    view = session.view()
    assert view.change.models, "this script should build a model"

    rows = _rows(client.get("/changed").get_data(as_text=True))

    for model in view.change.models:
        mine = [view.statements[i].raw.text for i in sorted(model.source_indices)]
        assert _blocks(rows[model.name])[: len(mine)] == mine


def test_the_statements_a_screen_pairs_against_come_from_its_own_read(
    walk: Walk, sql_script
) -> None:
    """One read, so the models and the statements cannot disagree.

    The failure this closes: the walk's source is a directory, `ingest`
    resolves one as `sorted(rglob("*.sql"))`, and a file renamed between two
    reads shifts every index while leaving the count identical -- each model
    beside another model's SQL, with nothing out of range to notice. A view
    carries the statements its own change was built from, so there is no
    second read to drift.
    """
    app, client, session = walk(sql_script(CHAINED_INSERTS))
    view = session.view()

    assert view.statements, "a view carries the statements it was built from"
    for model in view.change.models:
        for index in model.source_indices:
            assert 0 <= index < len(view.statements)
    # The same call twice is the same answer: nothing here re-reads the path
    # to answer a question the first read already answered.
    assert session.view().statements == view.statements


def test_a_grant_folded_into_a_model_is_shown_beside_it(walk: Walk, sql_script) -> None:
    """A GRANT puts a `grants={...}` line in the model file, and it is not
    one of the model's `source_indices` -- `grants_pass` attaches it to a
    draft that already exists.

    So it belongs to neither the model's own sources nor the caveats
    screen's orphans, and without `folded_indices` this screen showed a
    reader a line of config with nothing on the page saying which of their
    statements asked for it -- on the one screen whose question is "is this
    still my query?".
    """
    granted = (
        "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders;\n"
        "GRANT SELECT ON revenue_events TO analyst;\n"
    )
    app, client, session = walk(sql_script(granted))
    view = session.view()
    (model,) = view.change.models
    assert model.grants, "the GRANT should have reached the model's config"
    assert model.folded_indices, "the GRANT should be recorded as folded into it"

    row = _rows(client.get("/changed").get_data(as_text=True))[model.name]

    blocks = _blocks(row)
    assert view.statements[model.folded_indices[0]].raw.text in blocks
    assert "grants" in blocks[-1], "the after should carry the grants config"
