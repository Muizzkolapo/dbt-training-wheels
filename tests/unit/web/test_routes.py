"""The answer loop and the failure states, driven through the routes.

Spec section 8's route list, exactly: `/answer` with an unknown key, with a
kind not among that Decision's options, and with a key whose Decision carries
no question -- each refused, and none of them leaving the session unable to
render the next screen. Plus section 7's two screen states: a conversion with
nothing to answer still renders and still offers the write action, and a
statement that failed to parse renders its classification in place while the
rest of the page renders around it.

Every refusal is asserted three ways: the status, the answers dict, and the
next `GET /`. A 400 that recorded the answer anyway, or that left the session
raising on every later render, is the failure this list exists to catch --
"none crashing the session" is the requirement, not "none returning 200".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.web.conftest import Walk
from tests.unit.web.helpers import (
    NO_QUESTIONS,
    ONE_APPEND_ELSEWHERE,
    ONE_MERGE,
    UNPARSEABLE_AND_APPEND,
)
from tests.unit.web.page import read

from dbtw.web import Session


def _first_question_key(session: Session) -> str:
    (first, *_) = session.view().questions
    return first.key


def _silent_decision_key(session: Session) -> str:
    """A Decision this conversion records without asking anything.

    Read off the conversion rather than spelled out: spec 3.3's trap is that
    such a Decision can be Tier 2 and can carry `chosen`, and this
    conversion carries exactly one of those.
    """
    return next(d.key for d in session.view().change.decisions if not d.question)


def test_an_answer_is_recorded_and_redirects_to_the_question_it_answered(
    walk: Walk, walk_sql: Path
) -> None:
    """POST/redirect/GET: the answer is applied, and the response is a
    redirect, so a refresh re-renders rather than re-answering.

    Both questions, because the redirect target is the screen that was
    answered and not the first one: the reader is meant to see their own
    answer applied, and the worked example under an append answer and under a
    merge are two different tables.
    """
    _app, client, session = walk(walk_sql)
    keys = [decision.key for decision in session.view().questions]

    first = client.post("/answer", data={"key": keys[0], "kind": "append"})
    assert first.status_code == 302
    assert first.headers["Location"] == "/questions/0"

    second = client.post("/answer", data={"key": keys[1], "kind": "append"})
    assert second.status_code == 302
    assert second.headers["Location"] == "/questions/1"

    assert session.answers == {keys[0]: ("append", ()), keys[1]: ("append", ())}
    assert client.get("/questions/1").status_code == 200


def test_an_unknown_key_is_refused_and_the_walk_still_renders(walk: Walk, walk_sql: Path) -> None:
    _app, client, session = walk(walk_sql)

    response = client.post("/answer", data={"key": "tier2.append.nowhere.sql:0", "kind": "append"})

    assert response.status_code == 400
    assert "tier2.append.nowhere.sql:0" in response.get_data(as_text=True)
    assert session.answers == {}
    assert client.get("/").status_code == 200
    assert client.get("/questions/0").status_code == 200


def test_a_kind_the_question_does_not_offer_is_refused_and_the_walk_still_renders(
    walk: Walk, walk_sql: Path
) -> None:
    """`var` is a real `OptionKind` -- the answer a script variable's question
    offers -- and no incremental question offers it. A kind that is not an
    OptionKind at all would be refused one step earlier and would prove less.
    """
    _app, client, session = walk(walk_sql)
    key = _first_question_key(session)

    response = client.post("/answer", data={"key": key, "kind": "var"})

    assert response.status_code == 400
    assert "var" in response.get_data(as_text=True)
    assert session.answers == {}
    assert client.get("/").status_code == 200


def test_a_key_whose_decision_asks_nothing_is_refused_and_the_walk_still_renders(
    walk: Walk, walk_sql: Path
) -> None:
    _app, client, session = walk(walk_sql)
    key = _silent_decision_key(session)

    response = client.post("/answer", data={"key": key, "kind": "append"})

    assert response.status_code == 400
    assert "asks no question" in response.get_data(as_text=True)
    assert session.answers == {}
    assert client.get("/").status_code == 200


def test_an_answer_missing_its_key_or_its_kind_is_refused(walk: Walk, walk_sql: Path) -> None:
    """Refused by the session, in the session's words, and not by a check in
    front of it: an empty key is a key this conversion does not ask, and an
    empty kind is a kind no question offers. A gate here would have been a
    second copy of two rules that already refuse loudly.
    """
    _app, client, session = walk(walk_sql)
    key = _first_question_key(session)

    no_key = client.post("/answer", data={"kind": "append"})
    assert no_key.status_code == 400
    assert "no question with key ''" in read(no_key.get_data(as_text=True)).text

    no_kind = client.post("/answer", data={"key": key})
    assert no_kind.status_code == 400
    assert "offers no '' option" in read(no_kind.get_data(as_text=True)).text

    assert session.answers == {}
    assert client.get("/").status_code == 200


def test_a_merge_answer_with_no_columns_is_refused_and_the_walk_still_renders(
    walk: Walk, walk_sql: Path
) -> None:
    """The append question's merge options carry a `columns_prompt`, so an
    answer of that kind without columns is one `answer_for` refuses. The
    refusal reaches the screen rather than a traceback.
    """
    _app, client, session = walk(walk_sql)
    key = next(d.key for d in session.view().questions if ".append." in d.key)

    response = client.post("/answer", data={"key": key, "kind": "merge"})

    assert response.status_code == 400
    assert "got no columns" in response.get_data(as_text=True)
    assert session.answers == {}
    assert client.get("/").status_code == 200


def test_columns_reach_the_answer_from_the_picker_and_from_the_free_text_field(
    walk: Walk, walk_sql: Path
) -> None:
    """Both inputs feed one answer, ticked columns first, and a name typed
    twice is sent once -- `assemble` is handed the key the user named, not a
    list with a repeat in it.
    """
    _app, client, session = walk(walk_sql)
    key = next(d.key for d in session.view().questions if ".append." in d.key)

    response = client.post(
        "/answer",
        data={
            "key": key,
            "kind": "merge",
            "columns": ["event_id"],
            "typed": "occurred_at, event_id",
        },
    )

    assert response.status_code == 302
    assert session.answers == {key: ("merge", ("event_id", "occurred_at"))}
    (model,) = [m for m in session.view().change.models if m.name == "stg_events"]
    assert model.unique_key == ("event_id", "occurred_at")


def test_an_answer_can_be_changed_after_it_has_been_given(walk: Walk, walk_sql: Path) -> None:
    """The trap Task 5 measured: once an answer is applied the rebuilt options
    name their key and carry no `columns_prompt` at all. An answer loop that
    read the requirement off the rendered option would have no way to send
    columns a second time, and the only answer still reachable would be
    "append". The route sends them, and the key changes.
    """
    _app, client, session = walk(walk_sql)
    key = next(d.key for d in session.view().questions if ".append." in d.key)

    assert (
        client.post("/answer", data={"key": key, "kind": "merge", "typed": "event_id"}).status_code
        == 302
    )
    assert (
        client.post(
            "/answer", data={"key": key, "kind": "merge_checked", "typed": "occurred_at"}
        ).status_code
        == 302
    )

    assert session.answers == {key: ("merge_checked", ("occurred_at",))}
    (model,) = [m for m in session.view().change.models if m.name == "stg_events"]
    assert model.unique_key == ("occurred_at",)


def test_a_question_index_this_conversion_does_not_have_is_not_a_screen(
    walk: Walk, walk_sql: Path
) -> None:
    _app, client, session = walk(walk_sql)
    assert len(session.view().questions) == 2
    assert client.get("/questions/2").status_code == 404


def test_a_conversion_with_nothing_to_answer_renders_and_offers_the_write_action(
    walk: Walk, sql_script, tmp_path: Path
) -> None:
    """Spec section 7: this is the common case for a clean script and must not
    look like an error. No question screens, every other screen renders, and
    the write action is there.
    """
    _app, client, session = walk(sql_script(NO_QUESTIONS))

    assert session.view().questions == ()
    for url in ("/", "/caveats", "/files", "/done"):
        assert client.get(url).status_code == 200, url
    assert client.get("/questions/0").status_code == 404

    done = client.get("/done").get_data(as_text=True)
    assert 'action="/write"' in done
    assert 'method="post"' in done


def test_a_statement_that_fails_to_parse_renders_in_place_and_the_rest_still_renders(
    walk: Walk, sql_script
) -> None:
    """Spec section 7's first row. One bad statement does not blank the page:
    its `unsupported` classification and the parser's own reason render, and
    the model built from the statement beside it renders too.
    """
    _app, client, session = walk(sql_script(UNPARSEABLE_AND_APPEND))
    ((_, statement),) = session.view().change.pending

    page = client.get("/caveats").get_data(as_text=True)
    assert statement.kind == "unsupported"
    assert "unsupported" in page
    assert "could not parse" in page
    assert statement.raw.text in page

    files = client.get("/files").get_data(as_text=True)
    assert "stg_revenue_events" in files


def test_a_held_answer_the_script_no_longer_asks_for_is_named_and_can_be_dropped(
    walk: Walk, sql_script
) -> None:
    """The one state where every other accessor raises. The screen says which
    answers it is about and offers the way out, rather than showing an error
    with no subject -- `stale_answers` is the accessor that still works.
    """
    sql = sql_script(ONE_APPEND_ELSEWHERE)
    _app, client, session = walk(sql)
    key = _first_question_key(session)
    assert client.post("/answer", data={"key": key, "kind": "append"}).status_code == 302

    sql.write_text(ONE_MERGE, encoding="utf-8")

    stranded = client.get("/")
    assert stranded.status_code == 409
    assert key in stranded.get_data(as_text=True)

    dropped = client.post("/stale")
    assert dropped.status_code == 302
    assert dropped.headers["Location"] == "/"
    assert session.answers == {}
    assert client.get("/").status_code == 200


def test_dropping_nothing_is_not_a_way_to_lose_an_answer(walk: Walk, walk_sql: Path) -> None:
    """`POST /stale` while nothing is stale must not clear the answers that
    are good -- `drop_stale_answers` drops what `stale_answers` names, and
    nothing else.
    """
    _app, client, session = walk(walk_sql)
    key = _first_question_key(session)
    client.post("/answer", data={"key": key, "kind": "append"})

    assert client.post("/stale").status_code == 302
    assert session.answers == {key: ("append", ())}


def test_the_write_route_is_not_served_by_this_build(walk: Walk, walk_sql: Path) -> None:
    """The done screen offers the write action; writing is the next task's,
    and the guard that keeps a conversion out of the user's own project lives
    on the CLI path only (`_refuse_output_inside_project`). Registering a
    route that wrote without it is the one thing this build must not do, so
    the route is absent and says so by being absent.
    """
    _app, client, _session = walk(walk_sql)
    assert client.post("/write").status_code == 404


@pytest.mark.parametrize(
    "url", ["/", "/questions/0", "/questions/1", "/caveats", "/files", "/done"]
)
def test_every_screen_of_the_walk_renders(walk: Walk, walk_sql: Path, url: str) -> None:
    _app, client, _session = walk(walk_sql)
    assert client.get(url).status_code == 200
