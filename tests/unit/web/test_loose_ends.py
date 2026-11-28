"""Four things found by driving the walk with clicks, end to end.

Each one is small, and each one is a place where the screen told the reader
something false or something they could not use: a count in the wrong
number, an engine key in a sentence meant for a person, a result page with
no address of its own, and one word meaning two things on two screens.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.web.conftest import Walk
from tests.unit.web.helpers import NO_QUESTIONS
from tests.unit.web.page import read

from dbtw.web.app import noun


def test_a_count_of_one_names_one_thing() -> None:
    assert noun(1, "model") == "model"
    assert noun(0, "model") == "models"
    assert noun(2, "model") == "models"


def test_the_start_screen_says_1_model_not_1_models(walk: Walk, sql_script) -> None:  # type: ignore[no-untyped-def]
    _app, client, session = walk(sql_script(NO_QUESTIONS))
    assert len(session.view().change.models) == 1

    text = read(client.get("/").get_data(as_text=True)).text

    assert "1 model" in text
    assert "1 models" not in text


def test_a_refused_answer_names_the_table_and_not_the_engine_key(
    walk: Walk, walk_sql: Path
) -> None:
    """Found by pressing a merge button with no column ticked. The refusal
    read "tier2.append.../walk.sql:3's 'merge' option asks ..." -- a key that
    exists for the engine to match answers by, shown to a person.
    """
    _app, client, session = walk(walk_sql)
    question = next(d for d in session.view().questions if ".append." in d.key)
    assert question.subject is not None

    body = client.post("/answer", data={"key": question.key, "kind": "merge"}).get_data(
        as_text=True
    )

    assert question.key not in body
    assert question.subject.table in body
    assert "got no columns" in body


def test_the_write_lands_on_a_page_with_its_own_address(walk: Walk, walk_sql: Path) -> None:
    """POST, redirect, GET -- the shape `/answer` already has. Deliver and
    push are held to it in test_push_route. Rendered
    straight from the POST, the result had no address: a refresh asked the
    browser to send the form again, and Back landed on a form submission.
    """
    _app, client, _session = walk(walk_sql)

    written = client.post("/write")

    assert written.status_code == 302
    assert written.headers["Location"].endswith("/written")
    page = client.get("/written")
    assert page.status_code == 200
    assert "CONVERSION_REPORT.md" in page.get_data(as_text=True)


def test_the_written_page_before_anything_is_written_goes_to_the_last_screen(
    walk: Walk, walk_sql: Path
) -> None:
    _app, client, _session = walk(walk_sql)

    early = client.get("/written")

    assert early.status_code == 302
    assert early.headers["Location"].endswith("/done")


def test_a_saved_description_is_marked_described_not_written(walk: Walk, walk_sql: Path) -> None:
    """ "Written" is also the heading of the page that lists the files on
    disk. On the describe screen it meant only that a sentence was saved,
    and a reader could take it for the model's file having been written.
    """
    _app, client, session = walk(walk_sql)
    name = session.view().change.models[0].name
    assert (
        client.post("/describe", data={"model": name, "text": "One row per event."}).status_code
        == 302
    )

    html = client.get("/describe").get_data(as_text=True)

    assert '<span class="mark" data-item="described">Described</span>' in html
    assert ">Written<" not in html


def test_the_written_page_does_not_show_a_write_the_conversion_has_moved_past(
    walk: Walk, walk_sql: Path
) -> None:
    """Write, then answer a question differently: the files on disk are the
    old conversion's. The page listing them as this conversion's result
    would be the screen disagreeing with the conversion it sits beside, so it
    sends the reader to the screen that writes the new one.
    """
    _app, client, session = walk(walk_sql)
    assert client.post("/write").status_code == 302
    assert client.get("/written").status_code == 200
    key = next(d.key for d in session.view().questions if ".append." in d.key)
    changed = client.post("/answer", data={"key": key, "kind": "merge", "typed": "event_id"})
    assert changed.status_code == 302

    stale = client.get("/written")

    assert stale.status_code == 302
    assert stale.headers["Location"].endswith("/done")
