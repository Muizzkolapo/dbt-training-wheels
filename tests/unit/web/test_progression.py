"""The walk's tenth conversion of a project does not read like its first.

The rail defines the dbt words on the screen in front of the reader. Left
alone it defines them identically every conversion, forever -- and the
walkthroughs behind this design found four of five readers never reached a
glossary block at all, which is what repeating one verbatim does to the fifth.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.web.conftest import Walk

from dbtw.core.progress import FULL_SHOWINGS, Progress, load_progress, save_progress

ONE_APPEND = "INSERT INTO totals SELECT order_id, amount FROM raw.orders;\n"


def _learned_everything(project: Path, state: Path) -> None:
    """A reader nine conversions into this project, for every word."""
    from dbtw.core.teach import GLOSSARY

    save_progress(
        project,
        Progress(conversions=9, shown={term.name: FULL_SHOWINGS for term in GLOSSARY}),
        path=state,
    )


def test_a_first_walk_defines_the_words_on_the_screen(
    walk: Walk, sql_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _app, client, _session = walk(sql_script(ONE_APPEND), out=tmp_path / "out")

    page = client.get("/").get_data(as_text=True)

    assert 'data-item="term"' in page
    assert 'data-item="met"' not in page


def test_a_walk_for_a_project_that_has_met_the_words_names_them_instead(
    walk: Walk, sql_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _app, client, session = walk(sql_script(ONE_APPEND), out=tmp_path / "out")
    _learned_everything(session.project, state / "dbtw" / "learned.json")

    page = client.get("/").get_data(as_text=True)

    assert 'data-item="met"' in page, "the words are still named"
    assert 'data-item="term"' not in page, "and not defined again"
    # One heading, not a heading over nothing followed by a sub-heading: with
    # every word already met the card is the one list, and it says so in a line.
    assert "All explained in earlier conversions of this project." in page
    assert "<h3>" not in page.split('class="glossary"')[1]


def test_a_folded_word_keeps_its_meaning_one_click_away(
    walk: Walk, sql_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Named, not dropped. A reader who has forgotten `ref()` must not have to
    find a walk from three conversions ago to look it up.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _app, client, session = walk(sql_script(ONE_APPEND), out=tmp_path / "out")
    _learned_everything(session.project, state / "dbtw" / "learned.json")

    page = client.get("/").get_data(as_text=True)

    met = page.split('class="terms met"')[1]
    assert "<details>" in met and "<p>" in met


def test_writing_records_what_this_walk_explained(
    walk: Walk, sql_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _app, client, session = walk(sql_script(ONE_APPEND), out=tmp_path / "out")

    assert client.post("/write").status_code == 200

    recorded = load_progress(session.project, path=state / "dbtw" / "learned.json")
    assert recorded.conversions == 1
    assert recorded.shown, "a walk that wrote a report explained something"


def test_nothing_is_recorded_until_the_write_action_is_pressed(
    walk: Walk, sql_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the walk is not converting with it. Recording on render would
    retire a word after three glances at a question screen, and would write to
    the reader's disk on a GET.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _app, client, _session = walk(sql_script(ONE_APPEND), out=tmp_path / "out")

    for path in ("/", "/changed", "/describe"):
        client.get(path)

    assert not (state / "dbtw" / "learned.json").exists()


def test_no_remember_defines_everything_and_records_nothing(
    walk: Walk, sql_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _app, client, session = walk(sql_script(ONE_APPEND), out=tmp_path / "out")
    _learned_everything(session.project, state / "dbtw" / "learned.json")
    client.application.config["dbtw_source"].remember = False

    page = client.get("/").get_data(as_text=True)
    assert client.post("/write").status_code == 200

    assert 'data-item="term"' in page, "every word is defined in full"
    assert 'data-item="met"' not in page
    unchanged = load_progress(session.project, path=state / "dbtw" / "learned.json")
    assert unchanged.conversions == 9, "the write recorded nothing"


def test_the_file_preview_shows_the_report_the_write_will_write(
    walk: Walk, sql_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview and the file must agree. They are produced by two `emit`
    calls, and a preview built with a different record would show a glossary
    the written report does not have -- the precise mismatch that cost this
    page its trust once already.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _app, client, session = walk(sql_script(ONE_APPEND), out=tmp_path / "out")
    _learned_everything(session.project, state / "dbtw" / "learned.json")

    preview = client.get("/files").get_data(as_text=True)
    assert client.post("/write").status_code == 200
    out = client.application.config["dbtw_source"].out
    written = (out / "CONVERSION_REPORT.md").read_text(encoding="utf-8")

    assert "Words you have met before" in written
    assert "Words you have met before" in preview


def test_a_screen_with_new_words_and_old_ones_keeps_them_apart(
    walk: Walk, sql_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shape every conversion between the first and the last has: some
    words explained here, the rest named because this project met them
    already. The two lists need a heading between them, which the all-met
    card must not have and this one must.
    """
    from dbtw.core.teach import GLOSSARY, terms_in

    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _app, client, session = walk(sql_script(ONE_APPEND), out=tmp_path / "out")

    # Which words this screen uses is the screen's business, so it is read off
    # the screen rather than guessed: leaving out a word the page never says
    # would build the all-met card again and test nothing.
    # The files screen rather than the first: it names nine dbt words where
    # the opening screen names one, and a screen with one word can only ever
    # produce the all-met card or the all-new one.
    first_run = client.get("/files").get_data(as_text=True)
    here = terms_in(first_run.split('class="glossary"')[1])
    assert here, "this screen has to use at least one dbt word for the test to mean anything"
    still_new = here[0].name

    save_progress(
        session.project,
        Progress(
            conversions=9,
            shown={t.name: FULL_SHOWINGS for t in GLOSSARY if t.name != still_new},
        ),
        path=state / "dbtw" / "learned.json",
    )
    card = client.get("/files").get_data(as_text=True).split('class="glossary"')[1]

    assert 'data-item="term"' in card, f"{still_new} is defined"
    assert 'data-item="met"' in card, "the met words are named"
    assert "Explained in earlier conversions" in card, "and the two are kept apart"
    assert "All explained in earlier conversions" not in card
