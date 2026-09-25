"""The walk with the asking collapsed onto one screen.

The design's Mode toggle, and its promise: "same conversion, everything on
one screen, none of this column." Both halves are tested here -- that the one
screen really holds every question and every model, and that switching to it
changes nothing about the conversion, because a mode that quietly converted
differently would be two products wearing one name.

The guided walk's own rules are not restated here. `test_screens.py` walks
the guided walk and excludes this screen for that reason; the cross-cutting
checks it owns -- no authored prose, every count the length of its list,
every position the position it sits in -- are run over this screen at the
bottom of this file, through the helpers that own them.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.web.conftest import Walk
from tests.unit.web.engine_strings import engine_strings, written_files
from tests.unit.web.page import normalised, read


def _direct(client) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/mode", data={"mode": "direct", "back": "/"}).status_code == 302


def test_the_rail_swaps_the_asking_screens_for_one(walk: Walk, walk_sql: Path) -> None:
    """Guided has a screen per question and a screen for the models; direct
    has one. Everything that *reports* what the conversion did is the same in
    both, because reading is not the part a comfortable reader collapses.
    """
    app, client, session = walk(walk_sql)
    guided = read(client.get("/").get_data(as_text=True))
    guided_links = set(guided.links)

    _direct(client)
    direct = read(client.get("/").get_data(as_text=True))

    direct_links = set(direct.links)
    assert "/everything" in direct_links
    assert not [link for link in direct_links if link.startswith("/questions/")]
    assert "/describe" not in direct_links
    # the reporting screens are untouched
    for url in ("/project", "/", "/caveats", "/changed", "/files", "/done"):
        assert url in guided_links and url in direct_links, url


def test_the_one_screen_holds_every_question_and_every_model(walk: Walk, walk_sql: Path) -> None:
    app, client, session = walk(walk_sql)
    _direct(client)
    view = session.view()
    assert len(view.questions) > 1, "this conversion should ask more than one question"

    page = read(client.get("/everything").get_data(as_text=True))

    assert sum(1 for item in page.items if item == "ask") == len(view.questions)
    assert sum(1 for item in page.items if item == "model") == len(view.change.models)
    shown = {normalised(run) for run in page.engine}
    for question in view.questions:
        assert normalised(question.plain_question) in shown, question.key
    for model in view.change.models:
        assert model.name in shown


def test_a_question_can_be_answered_from_the_one_screen(walk: Walk, walk_sql: Path) -> None:
    app, client, session = walk(walk_sql)
    _direct(client)
    key = next(d.key for d in session.view().questions if ".append." in d.key)

    answered = client.post(
        "/answer", data={"key": key, "kind": "merge_checked", "typed": "event_id"}
    )

    assert answered.status_code == 302
    assert session.answers[key] == ("merge_checked", ("event_id",))
    assert session.view().change.tests, "the answer did not reach the conversion"


def test_a_model_can_be_described_from_the_one_screen(walk: Walk, walk_sql: Path) -> None:
    app, client, session = walk(walk_sql)
    _direct(client)
    name = session.view().change.models[0].name

    saved = client.post(
        "/describe",
        data={
            "model": name,
            "text": "One row per thing.",
            "tags": "finance",
            "materialization": "view",
        },
    )

    assert saved.status_code == 302
    assert session.descriptions[name] == "One row per thing."
    assert session.tags[name] == ("finance",)
    (model,) = [m for m in session.view().change.models if m.name == name]
    assert model.materialization == "view"


def test_the_mode_changes_the_screens_and_not_the_conversion(walk: Walk, walk_sql: Path) -> None:
    """The design's own promise, and the reason `mode` is held on `Source`
    rather than passed into `assemble`: the same answers produce the same
    files in either mode, byte for byte.
    """
    app, client, session = walk(walk_sql)
    key = next(d.key for d in session.view().questions if ".append." in d.key)
    name = session.view().change.models[0].name
    assert (
        client.post(
            "/answer", data={"key": key, "kind": "merge_checked", "typed": "event_id"}
        ).status_code
        == 302
    )
    assert (
        client.post(
            "/describe", data={"model": name, "text": "One row.", "tags": "daily"}
        ).status_code
        == 302
    )
    guided_files = written_files(session)
    guided_change = session.view().change

    _direct(client)

    assert written_files(session) == guided_files
    assert session.view().change == guided_change


def test_switching_mode_lands_the_reader_where_they_pressed_it(walk: Walk, walk_sql: Path) -> None:
    """Switching on the caveats screen leaves them reading caveats, not at
    the start of a walk they had already come through."""
    app, client, session = walk(walk_sql)

    switched = client.post("/mode", data={"mode": "direct", "back": "/caveats"})

    assert switched.status_code == 302
    assert switched.headers["Location"].endswith("/caveats")


def test_a_back_that_leaves_this_walk_is_not_followed(walk: Walk, walk_sql: Path) -> None:
    """`back` is a value a form supplies, and following it anywhere would
    make this an open redirect on somebody's own machine."""
    app, client, session = walk(walk_sql)

    for escape in ("https://example.invalid/", "//example.invalid/", "nowhere"):
        switched = client.post("/mode", data={"mode": "direct", "back": escape})
        assert switched.status_code == 302
        assert switched.headers["Location"].endswith("/"), escape
        assert "example.invalid" not in switched.headers["Location"]


def test_a_mode_this_walk_does_not_have_changes_nothing(walk: Walk, walk_sql: Path) -> None:
    app, client, session = walk(walk_sql)
    source = app.config["dbtw_source"]

    assert client.post("/mode", data={"mode": "sideways", "back": "/"}).status_code == 302

    assert source.mode == "guided"


def test_the_asking_screens_send_a_direct_reader_to_the_one_place_to_answer(
    walk: Walk, walk_sql: Path
) -> None:
    """The routes stay reachable rather than 404ing: a reader who switched
    mode with a question screen open still has its address."""
    app, client, session = walk(walk_sql)
    _direct(client)

    for url in ("/questions/0", "/describe"):
        sent = client.get(url)
        assert sent.status_code == 302, url
        assert sent.headers["Location"].endswith("/everything"), url


def test_the_workbench_is_not_a_screen_of_the_guided_walk(walk: Walk, walk_sql: Path) -> None:
    app, client, session = walk(walk_sql)

    sent = client.get("/everything")

    assert sent.status_code == 302
    assert sent.headers["Location"].endswith("/")


def test_the_one_screen_is_held_to_the_rules_every_screen_is(
    walk: Walk, walk_sql: Path, out_dir: Path
) -> None:
    """The cross-cutting guards, run over this screen: nothing it marks as
    the engine's is text the engine never produced, nothing it writes reaches
    sentence length, every number is the length of the list beside it, and no
    digit answers to nothing.
    """
    app, client, session = walk(walk_sql)
    _direct(client)
    produced = engine_strings(session, out_dir)

    page = read(client.get("/everything").get_data(as_text=True))

    unclaimed = [run for run in page.engine if normalised(run) not in produced]
    assert not unclaimed, f"marks text the engine never produced: {unclaimed[:2]}"
    assert not page.sentences(5), f"authors prose: {page.sentences(5)}"
    assert page.counts, "shows no derived count"
    for name, shown in page.counts:
        listed = sum(1 for item in page.items if item == name)
        assert shown == str(listed), f"says {shown} {name} beside {listed}"
    assert not page.undeclared_numbers(), page.undeclared_numbers()
    assert page.scripts == ()


def test_the_write_action_is_not_gated_in_direct_mode_either(walk: Walk, walk_sql: Path) -> None:
    app, client, session = walk(walk_sql)
    _direct(client)

    done = read(client.get("/done").get_data(as_text=True))

    writing = [c for c in done.controls if c["form"] == "/write"]
    assert writing, "the write form offers nothing to press"
    assert not [c for c in writing if "required" in c]
    assert client.post("/write").status_code == 200
