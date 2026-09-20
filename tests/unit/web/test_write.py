"""The write action: what lands on disk, what refuses, and what it says.

Three things are being held here.

**The two paths stay honest (spec section 8).** Answering a question in the
browser and pressing write has to produce the same files as the CLI flag that
answers the same question -- otherwise the browser and the command line are
two converters. That comparison is narrower than it looks, and the narrowness
is the design rather than a gap: `dbtw convert` offers one blanket
`--unique-key`, which answers *every* append question in the run at once. So
a session with one append question answered "merge" on one column is
comparable to `dbtw convert --unique-key <column>` and nothing else is. The
answers with no CLI spelling are asserted on the emitted tree alone, each
with a line saying why there is no other side to compare against -- the
per-model expressiveness is the whole reason the browser surface exists
(spec 4.1).

**A write never reaches the user's own dbt project.** `emit` refuses an
out_dir that is the project, is inside it, or reaches it by another name, and
these drive that through the *route*. The CLI's own tests pass whatever this
route does, so they say nothing about it.

**A failure says so.** Spec section 7: the error, the path, and the models
still on screen, and never a half-written project reported as success.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.web.conftest import Walk
from tests.unit.web.engine_strings import written_files
from tests.unit.web.helpers import NO_QUESTIONS, ONE_APPEND
from tests.unit.web.page import normalised, read

from dbtw.cli.main import main
from dbtw.web import Session

_REPORT = "CONVERSION_REPORT.md"


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under `root`, by relative path, with its bytes."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _append_key(session: Session, table: str = "revenue_events") -> str:
    return next(d.key for d in session.view().questions if table in d.action)


def _answer(client, key: str, kind: str, typed: str = "") -> None:
    response = client.post("/answer", data={"key": key, "kind": kind, "typed": typed})
    assert response.status_code == 302, response.status_code


# --- what lands on disk


def test_the_write_action_writes_the_conversion_that_is_on_screen(
    walk: Walk, sql_script, out_dir: Path
) -> None:
    """The files screen shows what will be written; pressing write writes
    exactly that. Compared file by file against the preview, because a write
    that produced a different set from the one the reader inspected is the
    mismatch that cost this page the most trust."""
    _app, client, session = walk(sql_script(ONE_APPEND))
    previewed = read(client.get("/files").get_data(as_text=True))
    shown = [run for run in previewed.engine]

    assert client.post("/write").status_code == 200

    written = _tree(out_dir)
    assert written, "the write action wrote nothing"
    for relative in written:
        assert relative in shown, f"{relative} was written and never previewed"
    assert len([item for item in previewed.items if item == "file"]) == len(written)


def test_a_conversion_with_nothing_to_answer_can_be_written(
    walk: Walk, sql_script, out_dir: Path
) -> None:
    """Spec section 7's "nothing to answer" row: the write action is
    available immediately and must not look like an error."""
    _app, client, session = walk(sql_script(NO_QUESTIONS))
    assert session.view().questions == ()

    assert client.post("/write").status_code == 200
    assert "CONVERSION_REPORT.md" in _tree(out_dir)


def test_the_written_screen_names_the_directory_and_every_file(
    walk: Walk, sql_script, out_dir: Path
) -> None:
    written = read(_written(walk, sql_script(ONE_APPEND)))
    landed = _tree(out_dir)

    rendered = {run.strip() for run in written.engine}
    assert str(out_dir) in rendered, rendered
    for relative in landed:
        assert relative in rendered, f"{relative} landed and is not named"
    assert ("written", str(len(landed))) in written.counts


def _written(walk: Walk, sql: Path) -> str:
    _app, client, _session = walk(sql)
    response = client.post("/write")
    assert response.status_code == 200
    return response.get_data(as_text=True)


# --- section 8: the browser and the command line convert the same script the
#     same way, wherever the two can express the same answer


def test_an_answer_in_the_browser_writes_what_the_equivalent_cli_flag_writes(
    walk: Walk, sql_script, project_dir: Path, out_dir: Path, tmp_path: Path
) -> None:
    """The one comparison the two surfaces can actually make. `--unique-key`
    upgrades every append question in the run at once, so a script with one
    append question answered "merge" on one column is the case where the
    blanket flag and the per-model answer mean the same thing.

    File by file, contents included: the report is written from the same
    change, so a conversion that agreed on the models and disagreed on what
    it told the reader about them would still be two converters.
    """
    sql = sql_script(ONE_APPEND)
    _app, client, session = walk(sql)
    _answer(client, _append_key(session), "merge", typed="order_id")
    assert client.post("/write").status_code == 200

    cli_out = tmp_path / "cli-out"
    code = main(
        [
            "convert",
            str(sql),
            "--project",
            str(project_dir),
            "--out",
            str(cli_out),
            "--unique-key",
            "order_id",
        ]
    )
    assert code == 0

    ours, theirs = _tree(out_dir), _tree(cli_out)
    assert set(ours) == set(theirs)
    assert {name: body for name, body in ours.items() if name != _REPORT} == {
        name: body for name, body in theirs.items() if name != _REPORT
    }

    # The report is the one file that may differ, and it differs in exactly
    # one line: the Decision that records the upgrade names the input that
    # asked for it. That is not drift -- two reports naming the same input
    # would mean one of them was saying something it was not told -- so it is
    # pinned rather than excused.
    #
    # Pinned by position and by length, not by which lines are unique to one
    # side. Membership alone misses a second divergence whose text is not
    # unique -- a duplicated heading, or two lines swapped -- because every
    # line of it still occurs somewhere on the other side. Equal lengths plus
    # exactly one differing index says the two reports are the same report
    # with one sentence replaced, which is the actual claim.
    #
    # The index itself is deliberately not written out here: how far down the
    # report that Decision falls is a fact about the report's layout, not
    # about the two surfaces agreeing, and pinning it would fail on a new
    # glossary term.
    mine = ours[_REPORT].decode().splitlines()
    flagged = theirs[_REPORT].decode().splitlines()
    assert len(mine) == len(flagged), (len(mine), len(flagged))
    differing = [at for at, (a, b) in enumerate(zip(mine, flagged, strict=True)) if a != b]
    assert len(differing) == 1, [(at, mine[at], flagged[at]) for at in differing]
    (at,) = differing
    assert "--unique-key" in flagged[at] and "--unique-key" not in mine[at]
    assert "answered 'merge on a unique key'" in mine[at], mine[at]

    # Not vacuous: the answered tree really is different from the unanswered
    # one, so the agreement above is two converters agreeing rather than two
    # converters both ignoring the answer.
    plain_out = tmp_path / "plain-out"
    assert main(["convert", str(sql), "--project", str(project_dir), "--out", str(plain_out)]) == 0
    assert _tree(plain_out) != ours


def test_the_checked_answer_writes_a_dbt_test_the_cli_cannot_ask_for(
    walk: Walk, sql_script, out_dir: Path
) -> None:
    """No CLI side, deliberately: `--unique-key` upgrades an append to a
    merge and has no spelling for "and have dbt check that key", so there is
    no command line to compare this against. Asserted on the emitted tree
    alone for that reason, not because a comparison was overlooked."""
    _app, client, session = walk(sql_script(ONE_APPEND))
    _answer(client, _append_key(session), "merge_checked", typed="order_id")
    assert client.post("/write").status_code == 200

    written = _tree(out_dir)
    schema = written["models/staging/stg_revenue_events.yml"].decode()
    assert "unique" in schema, schema
    assert "not_null" not in schema, "a test nobody was offered was declared"
    assert "order_id" in written["models/staging/stg_revenue_events.sql"].decode()


TWO_APPENDS = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
    "INSERT INTO page_views SELECT order_id, ts FROM stg_views;\n"
)


def test_two_questions_answered_differently_write_two_different_models(
    walk: Walk, sql_script, out_dir: Path
) -> None:
    """No CLI side either: `--unique-key` answers every append question in
    the run at once, so it cannot express one model merging and another
    appending. This per-model expressiveness is the reason the browser
    surface exists (spec 4.1), so it is asserted on the emitted tree alone."""
    _app, client, session = walk(sql_script(TWO_APPENDS))
    _answer(client, _append_key(session, "revenue_events"), "merge", typed="order_id")
    _answer(client, _append_key(session, "page_views"), "append")
    assert client.post("/write").status_code == 200

    written = _tree(out_dir)
    merged = written["models/staging/stg_revenue_events.sql"].decode()
    appended = written["models/staging/stg_page_views.sql"].decode()
    assert "unique_key='order_id'" in merged, merged
    assert "unique_key" not in appended, appended
    assert "incremental_strategy='append'" in appended, appended


# --- the project the conversion was read from is never written into


def _snapshot_project(project: Path) -> dict[str, bytes]:
    return _tree(project)


@pytest.mark.parametrize(
    "where",
    ["the project itself", "a directory inside it", "a model path", "a symlink to it"],
)
def test_the_write_route_refuses_to_write_into_the_target_project(
    walk: Walk, sql_script, project_dir: Path, tmp_path: Path, where: str
) -> None:
    """Driven through the route, not through the CLI. PR #14 exists to stop
    this tool writing into the user's own dbt project, and the CLI's tests
    pass whatever this route does."""
    destinations = {
        "the project itself": project_dir,
        "a directory inside it": project_dir / "out",
        "a model path": project_dir / "models",
        "a symlink to it": tmp_path / "link",
    }
    if where == "a symlink to it":
        destinations[where].symlink_to(project_dir, target_is_directory=True)
    out = destinations[where]

    _app, client, _session = walk(sql_script(ONE_APPEND), out=out)
    before = _snapshot_project(project_dir)

    response = client.post("/write")

    assert response.status_code == 500, where
    assert _snapshot_project(project_dir) == before, where
    assert not (project_dir / "out").exists(), "the refusal created a directory in the project"


def test_the_refusal_screen_carries_the_error_the_path_and_the_models(
    walk: Walk, sql_script, project_dir: Path
) -> None:
    """Spec section 7's failure row, in full. A page saying only "failed"
    sends the reader back to a terminal they were not using."""
    _app, client, session = walk(sql_script(ONE_APPEND), out=project_dir)
    page = read(client.post("/write").get_data(as_text=True))

    rendered = " ".join(page.engine)
    assert "refusing to convert into the target project" in rendered, rendered
    assert str(project_dir.resolve()) in rendered, rendered
    # The models, still there: the reader can read what would have been
    # written while they fix where it was going.
    assert "models/staging/stg_revenue_events.sql" in rendered, rendered
    assert "incremental_strategy='append'" in rendered, rendered


def test_the_refusal_screen_carries_every_file_the_conversion_would_write(
    walk: Walk, walk_sql: Path, project_dir: Path
) -> None:
    """ "The models still on screen" is a claim about all of them.

    Held against `written_files` rather than against two hand-written
    literals: a conversion of this script writes models, a sources file and
    the report, and a page that rendered the first of them and stopped would
    satisfy any assertion naming one file -- while hiding the sources file
    and the report from the one screen section 7 requires to carry them. The
    count is held against the same set for the same reason: derived from a
    truncated list it is self-consistently wrong, and only a comparison with
    something outside the page can see that.

    This conversion, not ONE_APPEND's: five files rather than two, so the
    difference between "all of them" and "the first of them" is four.
    """
    _app, client, session = walk(walk_sql, out=project_dir)
    page = read(client.post("/write").get_data(as_text=True))

    expected = written_files(session)
    assert len(expected) > 2, "this script is meant to write more than a model and a report"
    rendered = {normalised(run) for run in page.engine}
    for path, contents in expected.items():
        assert path in rendered, f"{path} would be written and is not on the refusal screen"
        assert normalised(contents) in rendered, f"{path} is named with none of its contents"

    assert ("file", str(len(expected))) in page.counts
    assert len([item for item in page.items if item == "file"]) == len(expected)


def test_a_refused_write_is_never_reported_as_a_written_one(
    walk: Walk, sql_script, project_dir: Path
) -> None:
    """The word the reader scans for. A refusal headed "Written" is worse
    than no page at all."""
    _app, client, _session = walk(sql_script(ONE_APPEND), out=project_dir)
    page = read(client.post("/write").get_data(as_text=True))
    assert "Written" not in page.text, page.text[:400]


# --- a failure part-way through, and what the screen says about it


def test_a_write_that_fails_part_way_is_not_reported_as_success(
    walk: Walk, sql_script, out_dir: Path
) -> None:
    """A directory standing where the report's file belongs: the models are
    written first and land, and the report cannot be. The run left half a
    directory behind and the screen has to say so -- "never a half-written
    project reported as success" is the requirement, and a page that reported
    success because some files appeared would meet the letter of it and not
    the point."""
    (out_dir / "CONVERSION_REPORT.md").mkdir(parents=True)
    _app, client, _session = walk(sql_script(ONE_APPEND))

    response = client.post("/write")

    assert response.status_code == 500
    page = read(response.get_data(as_text=True))
    assert "Written" not in page.text
    # The model really did land, which is what makes this a half-written
    # directory rather than an untouched one.
    assert (out_dir / "models" / "staging" / "stg_revenue_events.sql").is_file()
    assert str(out_dir) in " ".join(page.engine)


def test_the_walk_still_renders_after_a_failed_write(
    walk: Walk, sql_script, project_dir: Path
) -> None:
    """Spec section 7: "the models still on screen". A failure that left the
    session unable to render anything would have taken the walk with it."""
    app, client, _session = walk(sql_script(ONE_APPEND), out=project_dir)
    assert client.post("/write").status_code == 500
    for url in ("/", "/questions/0", "/caveats", "/files", "/done"):
        assert client.get(url).status_code == 200, url


def test_a_failed_write_can_be_retried_and_still_fails(
    walk: Walk, sql_script, project_dir: Path
) -> None:
    """Nothing was recorded, so the second press is the first press again.
    A route that remembered the attempt rather than the write would report a
    project it never wrote."""
    _app, client, _session = walk(sql_script(ONE_APPEND), out=project_dir)
    before = _snapshot_project(project_dir)
    for _ in range(2):
        assert client.post("/write").status_code == 500
    assert _snapshot_project(project_dir) == before


# --- pressing it twice


def test_pressing_write_twice_does_not_write_twice(
    walk: Walk, sql_script, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A double click, or a refresh of the result. Counted at the destination
    rather than by comparing bytes: the same conversion written twice leaves
    identical bytes, so a byte comparison could never tell one write from
    two."""
    import dbtw.web.app as app_module

    writes: list[Path] = []
    real = app_module.emit

    def spy(change, ctx, target):  # type: ignore[no-untyped-def]
        writes.append(Path(target))
        return real(change, ctx, target)

    monkeypatch.setattr(app_module, "emit", spy)

    _app, client, _session = walk(sql_script(ONE_APPEND))
    for _ in range(2):
        assert client.post("/write").status_code == 200

    # `_previews` runs emit into a throwaway directory on the files screen and
    # on a failure page, so only the writes aimed at the destination count.
    assert [target for target in writes if target == out_dir] == [out_dir]


def test_answering_again_after_a_write_writes_again(walk: Walk, sql_script, out_dir: Path) -> None:
    """The other half, and the one that makes the guard above a guard rather
    than a lock: a reader who writes, goes back, changes an answer and writes
    again must get the conversion they now have. Reporting the first result
    over the second would be the page lying about what is on disk."""
    _app, client, session = walk(sql_script(ONE_APPEND))
    assert client.post("/write").status_code == 200
    assert "unique_key" not in _tree(out_dir)["models/staging/stg_revenue_events.sql"].decode()

    _answer(client, _append_key(session), "merge", typed="order_id")
    assert client.post("/write").status_code == 200

    model = _tree(out_dir)["models/staging/stg_revenue_events.sql"].decode()
    assert "unique_key='order_id'" in model, model


# --- the states the walk can be in when the action is pressed


def test_a_stale_answer_stops_the_write_and_says_which_answer(
    walk: Walk, sql_script, out_dir: Path
) -> None:
    """The script was edited under a standing answer, so this conversion
    cannot be run at all. Writing whatever the last run produced would write
    a conversion nobody is looking at."""
    sql = sql_script("INSERT INTO daily_totals SELECT order_id, amount FROM stg_orders;\n")
    _app, client, session = walk(sql)
    (question,) = session.view().questions
    _answer(client, question.key, "append")
    sql.write_text("SELECT id INTO dim_people FROM raw_people;\n", encoding="utf-8")

    response = client.post("/write")

    assert response.status_code == 409
    assert not out_dir.exists(), "a stranded conversation wrote to disk"
    assert question.key in read(response.get_data(as_text=True)).text


def test_the_walk_still_renders_after_a_write(walk: Walk, sql_script) -> None:
    """A write is terminal for the conversion, not for the pages. A reader
    who writes and then goes back to read a caveat has to find one."""
    _app, client, _session = walk(sql_script(ONE_APPEND))
    assert client.post("/write").status_code == 200
    for url in ("/", "/questions/0", "/caveats", "/files", "/done"):
        assert client.get(url).status_code == 200, url


def test_the_done_screen_names_where_the_write_will_go(walk: Walk, sql_script, out_dir: Path):
    """Before the button, not after it. The reader presses one control in the
    whole walk that touches their disk, and where it goes is not something
    they can be expected to remember from the command they typed."""
    _app, client, _session = walk(sql_script(ONE_APPEND))
    page = read(client.get("/done").get_data(as_text=True))
    assert str(out_dir) in {run.strip() for run in page.engine}


def test_nothing_is_written_until_the_action_is_pressed(walk: Walk, sql_script, out_dir: Path):
    """Every screen of the walk runs the conversion; none of them may leave
    it on disk. `_previews` emits into a directory it throws away, and this
    is what says the destination is not that directory."""
    _app, client, _session = walk(sql_script(ONE_APPEND))
    for url in ("/", "/questions/0", "/caveats", "/files", "/done"):
        assert client.get(url).status_code == 200
    assert not out_dir.exists()


# --- the project copy is never the fixture


def test_these_tests_convert_against_a_copy(
    project_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Every test here writes, and several of them aim the write at
    `project_dir` on purpose. A fixture reached in place would be damaged by
    the guard's first regression, and the damage would be committed.

    Three assertions, and the third is the one that matters: the copy is a
    different directory, it is inside pytest's own temporary area, and it
    currently holds exactly what the fixture holds -- so a test that damaged
    it would be damaging something that started out identical to the
    repository's own data.

    Against the factory's base rather than `tmp_path`, because `project_dir`
    no longer comes from `tmp_path`: that fixture is named after the running
    test, and the app bar puts the project path on every screen, so a test's
    own name was reaching the page. See the note on `project_dir`.
    """
    fixture = Path(__file__).parents[2] / "fixtures" / "projects" / "jaffle_shop"
    assert project_dir.resolve() != fixture.resolve()
    assert tmp_path_factory.getbasetemp().resolve() in project_dir.resolve().parents
    assert _tree(project_dir) == _tree(fixture)
