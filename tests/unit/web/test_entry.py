"""Bringing SQL in: the screen before the walk, and what it does with what
it is given.

`dbtw web` given a SQL_PATH opens a conversation over it at once. Given
none, there is nothing to convert yet, and this is the screen a reader meets
instead -- the design's "bring in a query". Everything here is about the
handover: what arrives, where it is staged, and what the walk looks like
once it has.

Why staging matters enough to have its own tests. A tier-2 `Decision.key`
embeds the path of the file its statement was read from (spec 11.7), so SQL
staged somewhere new on each run hands out keys that are invalid on the next
one, and every answer held against them is refused. The directory a paste
lands in is therefore made once and reused, and that is asserted rather than
assumed.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from tests.unit.web.helpers import ONE_APPEND, ONE_MERGE
from tests.unit.web.page import read

from dbtw.web import Source


@pytest.fixture
def unstarted(project_dir: Path, out_dir: Path):  # type: ignore[no-untyped-def]
    """The app as `dbtw web` builds it with no SQL_PATH: no conversation."""
    from dbtw.web.app import create_app

    source = Source(project=project_dir, out=out_dir)
    app = create_app(source)
    app.testing = True
    return app, app.test_client(), source


def test_a_walk_with_no_sql_yet_offers_the_screen_that_brings_some(unstarted) -> None:  # type: ignore[no-untyped-def]
    _app, client, source = unstarted
    assert source.session is None

    page = read(client.get("/source").get_data(as_text=True))

    (form,) = [f for f in page.forms if f.get("action") == "/source"]
    assert form.get("method") == "post"
    assert any(c["tag"] == "textarea" and c.get("name") == "pasted" for c in page.controls)
    assert any(c.get("type") == "file" and c.get("name") == "files" for c in page.controls)


def test_every_screen_of_the_walk_sends_a_reader_to_bring_sql_first(unstarted) -> None:  # type: ignore[no-untyped-def]
    """Not a 404 and not a traceback. Until there is a conversation there is
    nothing for any of these to render, and the one thing a reader can do
    about that is on one screen -- so that is where each of them points.
    """
    _app, client, _source = unstarted

    for url in ("/", "/questions/0", "/caveats", "/files", "/done"):
        response = client.get(url)
        assert response.status_code == 302, f"{url} -> {response.status_code}"
        assert response.headers["Location"].endswith("/source"), url


def test_pasting_a_query_opens_a_conversation_over_it(unstarted) -> None:  # type: ignore[no-untyped-def]
    _app, client, source = unstarted

    response = client.post("/source", data={"pasted": ONE_APPEND})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert source.session is not None
    assert [d.question for d in source.session.view().questions if d.question]
    assert client.get("/").status_code == 200


def test_an_uploaded_file_opens_the_same_conversation_a_paste_would(unstarted) -> None:  # type: ignore[no-untyped-def]
    """One path in, whichever control it came through: both end up as files
    in one directory, and `ingest` reads a directory as every .sql in it.
    """
    _app, client, source = unstarted

    uploaded = (io.BytesIO(ONE_APPEND.encode("utf-8")), "loads.sql")
    assert client.post("/source", data={"files": uploaded}).status_code == 302

    assert source.session is not None
    by_upload = [d.key.rsplit(":", 1)[-1] for d in source.session.view().questions]

    pasted = Source(project=source.project, out=source.out)
    pasted.start({"pasted.sql": ONE_APPEND})
    assert pasted.session is not None
    assert [d.key.rsplit(":", 1)[-1] for d in pasted.session.view().questions] == by_upload


def test_several_uploaded_files_convert_together(unstarted) -> None:  # type: ignore[no-untyped-def]
    """The design's folder of .sql files. `ingest` reads a directory, so more
    than one file is the ordinary case rather than a second code path.
    """
    _app, client, source = unstarted

    assert (
        client.post(
            "/source",
            data={
                "files": [
                    (io.BytesIO(ONE_APPEND.encode("utf-8")), "one.sql"),
                    (io.BytesIO(ONE_MERGE.encode("utf-8")), "two.sql"),
                ]
            },
        ).status_code
        == 302
    )

    assert source.session is not None
    questions = source.session.view().questions

    # One question per file, each keyed to the file its statement was read
    # from -- which is the whole of what "converted together" means here, and
    # is asserted on the key rather than on the count, so a run that asked two
    # questions about one file could not pass.
    asked_about = sorted(Path(d.key.split(".", 2)[-1].rsplit(":", 1)[0]).name for d in questions)
    assert asked_about == ["one.sql", "two.sql"]


def test_pressing_convert_with_nothing_in_the_box_is_refused_on_the_same_screen(unstarted) -> None:  # type: ignore[no-untyped-def]
    """Mid-action, so the reason belongs on the page the reader is already
    on -- the same shape as the answer loop's refusal, and for the same
    reason.
    """
    _app, client, source = unstarted

    response = client.post("/source", data={"pasted": "   \n  "})

    assert response.status_code == 400
    assert source.session is None
    page = read(response.get_data(as_text=True))
    assert any("Convert" in run for run in page.authored), "the form is still there to retry on"
    assert [f for f in page.forms if f.get("action") == "/source"]


def test_a_second_source_replaces_the_first_rather_than_joining_it(unstarted) -> None:  # type: ignore[no-untyped-def]
    """New SQL is a new conversation. A second one that left the first in
    place would convert both, and show a walk over a script the reader thinks
    they replaced.

    The first arrives as an upload and the second as a paste, so the two land
    under *different* names. Two pastes would both be `pasted.sql`, and the
    second would overwrite the first whether the directory were cleared or
    not -- an assertion that cannot fail. A mutation deleting the clearing is
    what said so.
    """
    _app, client, source = unstarted
    uploaded = (io.BytesIO(ONE_MERGE.encode("utf-8")), "uploaded.sql")
    assert client.post("/source", data={"files": uploaded}).status_code == 302
    assert source.session is not None
    first = source.session.view().change.models

    assert client.post("/source", data={"pasted": ONE_APPEND}).status_code == 302

    assert source.session is not None
    assert source.session.view().change.models != first
    staged = sorted(p.name for p in source.session.sql.iterdir())
    assert staged == ["pasted.sql"], f"the first source is still staged: {staged}"


def test_an_answer_given_before_a_new_paste_does_not_survive_it(unstarted) -> None:  # type: ignore[no-untyped-def]
    """An answer names a question of the run that asked it. The new run did
    not ask, so carrying the answer over would hold it against a question
    nobody was shown.
    """
    _app, client, source = unstarted
    assert client.post("/source", data={"pasted": ONE_APPEND}).status_code == 302
    assert source.session is not None
    (question,) = source.session.view().questions
    assert client.post("/answer", data={"key": question.key, "kind": "append"}).status_code == 302
    assert source.session.answers

    assert client.post("/source", data={"pasted": ONE_MERGE}).status_code == 302

    assert source.session is not None
    assert not source.session.answers
    assert client.get("/").status_code == 200


def test_the_staging_directory_does_not_move_between_pastes(unstarted) -> None:  # type: ignore[no-untyped-def]
    """Spec 11.7: a tier-2 key embeds the path its statement was read from.
    One directory for the life of the process is what keeps a key issued by
    one run valid on the next.
    """
    _app, client, source = unstarted
    assert client.post("/source", data={"pasted": ONE_APPEND}).status_code == 302
    assert source.session is not None
    first = source.session.sql

    assert client.post("/source", data={"pasted": ONE_MERGE}).status_code == 302

    assert source.session is not None
    assert source.session.sql == first


def test_bringing_sql_writes_nothing_to_the_destination(unstarted) -> None:  # type: ignore[no-untyped-def]
    """Staging is not writing. The walk writes when the write action is
    pressed and not before, and pasting is not that action.
    """
    _app, client, source = unstarted

    assert client.post("/source", data={"pasted": ONE_APPEND}).status_code == 302

    assert not source.out.exists(), f"{source.out} was created before the write action"


def test_a_walk_started_from_the_command_line_still_reaches_the_entry_screen(
    walk, walk_sql: Path
) -> None:  # type: ignore[no-untyped-def]
    """Supplying SQL_PATH means the conversation is open already; it does not
    mean a reader is stuck with it.
    """
    _app, client, _session = walk(walk_sql)

    assert client.get("/").status_code == 200
    page = read(client.get("/source").get_data(as_text=True))
    assert page.named_links.get("back") == "/"
