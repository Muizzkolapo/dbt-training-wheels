"""What a model is for, in the reader's own words.

The one thing in this walk the engine does not derive and must not: the SQL
says what a model computes, and no reading of it says what the model is *for*.
So these tests are about a field the engine carries and never fills -- that it
reaches the .yml the reader will commit, that a name the conversion does not
build is refused rather than dropped, and that an edit to the SQL underneath a
standing description leaves a way out instead of a traceback.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.web.conftest import Walk
from tests.unit.web.engine_strings import engine_strings, written_files
from tests.unit.web.helpers import CHAINED_INSERTS, ONE_APPEND_ELSEWHERE, ONE_MERGE
from tests.unit.web.page import normalised, read

from dbtw.core.assemble import UnknownAnswerError, UnknownModelError
from dbtw.web import Session


def _model(session: Session) -> str:
    """The one model this conversation builds."""
    (model,) = session.view().change.models
    return model.name


def test_a_description_reaches_the_conversion(sql_file: Path, project_dir: Path) -> None:
    session = Session(project=project_dir, sql=sql_file)
    name = _model(session)

    session.describe(name, "One row per revenue event, at order grain.")

    (described,) = session.view().change.descriptions
    assert described.model == name
    assert described.text == "One row per revenue event, at order grain."


def test_a_description_reaches_the_file_the_reader_will_commit(
    sql_file: Path, project_dir: Path
) -> None:
    """Not merely carried. A description that reached the change and not the
    .yml would be a field the walk collects and throws away.
    """
    session = Session(project=project_dir, sql=sql_file)
    name = _model(session)

    session.describe(name, "One row per revenue event, at order grain.")

    written = written_files(session)
    schema = next(text for path, text in written.items() if path.endswith(f"{name}.yml"))
    assert "One row per revenue event, at order grain." in schema


def test_a_description_naming_a_model_this_conversion_does_not_build_is_refused(
    sql_file: Path, project_dir: Path
) -> None:
    """Refused, and not held. A description recorded against a name no run
    carries makes every later render raise, with no screen left to get out
    from.
    """
    session = Session(project=project_dir, sql=sql_file)

    with pytest.raises(UnknownModelError):
        session.describe("not_a_model_here", "words about nothing")

    assert session.descriptions == {}
    assert session.view().change.descriptions == ()


def test_clearing_a_description_goes_through_the_same_refusal_as_writing_one(
    sql_file: Path, project_dir: Path
) -> None:
    """A blank box is an edit, not an exemption.

    The mutation this is here for records the blank straight into the dict --
    or pops it -- instead of running the conversion, which accepts a blank for
    a model this conversion does not build and says nothing.
    """
    session = Session(project=project_dir, sql=sql_file)
    name = _model(session)
    session.describe(name, "described once")

    session.describe(name, "   ")

    assert session.view().change.descriptions == ()
    with pytest.raises(UnknownModelError):
        session.describe("not_a_model_here", "")


def test_a_description_and_an_answer_are_applied_by_one_run(
    sql_file: Path, project_dir: Path
) -> None:
    """Both halves of the reader's contribution reach the same conversion.

    The failure this catches is a run that carries one and drops the other --
    a description applied to a run built without the answers, or answers
    resolved against a run that refuses the description.
    """
    session = Session(project=project_dir, sql=sql_file)
    name = _model(session)
    session.describe(name, "One row per revenue event.")

    (question,) = session.view().questions
    session.answer(question.key, "merge_checked", ("order_id",))

    change = session.view().change
    assert [d.text for d in change.descriptions] == ["One row per revenue event."]
    assert [(t.model, t.column, t.test) for t in change.tests] == [(name, "order_id", "unique")]


def test_a_question_is_still_answerable_while_a_description_is_held(
    sql_file: Path, project_dir: Path
) -> None:
    """The pristine run an answer is resolved against carries no descriptions.

    A session that fed its descriptions into that run too would be sound here
    and unsound the moment one goes stale: the run that resolves answers would
    refuse, and `stale_answers` -- the one accessor that still answers -- would
    refuse with it.
    """
    session = Session(project=project_dir, sql=sql_file)
    session.describe(_model(session), "One row per revenue event.")

    (question,) = session.view().questions
    session.answer(question.key, "append")

    assert session.answers[question.key] == ("append", ())


def test_a_description_stranded_by_an_edit_to_the_sql_is_named_and_droppable(
    sql_script, project_dir: Path
) -> None:
    """The description half of `stale_answers`, and the same way out.

    The session re-reads its inputs on every run, so an edit that retires a
    model retires the description written against it, and `assemble` then
    refuses the whole conversion. Every screen raises with it; this is the
    accessor that still answers.
    """
    sql = sql_script(ONE_APPEND_ELSEWHERE)
    session = Session(project=project_dir, sql=sql)
    stranded = _model(session)
    session.describe(stranded, "Daily totals, one row per day.")
    try:
        sql.write_text(ONE_MERGE, encoding="utf-8")

        assert session.stale_descriptions() == (stranded,)
        with pytest.raises(UnknownModelError):
            session.view()

        assert session.drop_stale_descriptions() == (stranded,)
        assert session.stale_descriptions() == ()
        assert session.view().change.descriptions == ()
    finally:
        sql.write_text(ONE_APPEND_ELSEWHERE, encoding="utf-8")


def test_a_session_stranded_on_both_is_stranded_on_its_answers_first(
    sql_script, project_dir: Path
) -> None:
    """An answer that cannot be resolved is a question this conversion no
    longer asks, and until it is dropped the models it would have named are
    not knowable. So `stale_descriptions` raises the same refusal every other
    accessor does, rather than reporting against a run it could not make.
    """
    sql = sql_script(ONE_APPEND_ELSEWHERE)
    session = Session(project=project_dir, sql=sql)
    session.describe(_model(session), "Daily totals, one row per day.")
    (question,) = session.view().questions
    session.answer(question.key, "append")
    try:
        sql.write_text(ONE_MERGE, encoding="utf-8")

        with pytest.raises(UnknownAnswerError):
            session.stale_descriptions()
        assert session.stale_answers() == (question.key,)
    finally:
        sql.write_text(ONE_APPEND_ELSEWHERE, encoding="utf-8")


def test_the_describe_screen_lists_every_model_this_conversion_builds(
    walk: Walk, walk_sql: Path
) -> None:
    app, client, session = walk(walk_sql)

    page = read(client.get("/describe").get_data(as_text=True))

    names = [model.name for model in session.view().change.models]
    assert len(names) > 1, "this conversion should build more than one model"
    rendered = {run for run in page.engine}
    for name in names:
        assert name in rendered, f"{name} is not on the screen that asks about it"
    assert sum(1 for item in page.items if item == "model") == len(names)


def test_a_description_written_on_the_screen_comes_back_on_it(walk: Walk, walk_sql: Path) -> None:
    """POST/redirect/GET, and the box holds what was written.

    A screen that took a description and rendered an empty box afterwards
    gives a reader no way to tell a saved description from a lost one.
    """
    app, client, session = walk(walk_sql)
    name = session.view().change.models[0].name

    saved = client.post("/describe", data={"model": name, "text": "One row per event."})

    assert saved.status_code == 302
    page = read(client.get("/describe").get_data(as_text=True))
    assert "One row per event." in page.engine
    assert session.descriptions[name] == "One row per event."


def test_the_describe_screen_counts_what_it_has_and_what_it_asks_for(
    walk: Walk, walk_sql: Path
) -> None:
    """The count is the length of the list beside it, and it moves.

    A page asserting only that a count matches its list, on a screen where
    both are always zero, is a page asserting nothing.
    """
    app, client, session = walk(walk_sql)
    names = [model.name for model in session.view().change.models]
    assert (
        client.post("/describe", data={"model": names[0], "text": "One row per event."}).status_code
        == 302
    )

    page = read(client.get("/describe").get_data(as_text=True))

    counts = dict(page.counts)
    assert counts["described"] == "1"
    assert counts["model"] == str(len(names))
    assert sum(1 for item in page.items if item == "described") == 1


def test_the_describe_screen_refuses_a_model_this_conversion_does_not_build(
    walk: Walk, walk_sql: Path
) -> None:
    """The refusal is the engine's own, on the screen the reader is on, and
    nothing is held.
    """
    app, client, session = walk(walk_sql)

    refused = client.post("/describe", data={"model": "not_a_model_here", "text": "words"})

    assert refused.status_code == 400
    assert session.descriptions == {}
    page = read(refused.get_data(as_text=True))
    assert any("not_a_model_here" in run for run in page.engine)


def test_a_description_given_on_the_screen_reaches_the_files_screen(
    walk: Walk, walk_sql: Path
) -> None:
    """The walk's own end-to-end claim: what the reader typed is in the file
    the files screen shows them, before anything is written.
    """
    app, client, session = walk(walk_sql)
    name = session.view().change.models[0].name
    assert (
        client.post("/describe", data={"model": name, "text": "One row per event."}).status_code
        == 302
    )

    page = read(client.get("/files").get_data(as_text=True))

    assert any("One row per event." in run for run in page.engine)


def test_a_walk_stranded_on_a_description_says_so_and_offers_the_way_out(
    walk: Walk, sql_script
) -> None:
    """The screen a reader meets when they edit their SQL under a description
    they have already written.

    Every screen refuses -- `assemble` will not build a conversion that
    carries a description of a model it no longer builds -- so what matters is
    that the one page they can reach names the model and gets them back into
    the walk. A traceback would name it too, and leave them outside.
    """
    sql = sql_script(ONE_APPEND_ELSEWHERE)
    app, client, session = walk(sql)
    (model,) = session.view().change.models
    assert (
        client.post("/describe", data={"model": model.name, "text": "Daily totals."}).status_code
        == 302
    )
    try:
        sql.write_text(ONE_MERGE, encoding="utf-8")

        stranded = client.get("/")
        assert stranded.status_code == 409
        page = read(stranded.get_data(as_text=True))
        assert model.name in page.engine

        assert client.post("/stale").status_code == 302
        assert session.descriptions == {}
        assert client.get("/").status_code == 200
    finally:
        sql.write_text(ONE_APPEND_ELSEWHERE, encoding="utf-8")


def test_one_edit_that_strands_both_names_each_before_it_drops_it(walk: Walk, sql_script) -> None:
    """An answer and a description retired by the same edit.

    Two screens and two presses, and that is the requirement rather than the
    cost. The screen offering "forget them" lists answer keys; a press that
    also dropped the description would have destroyed a sentence the reader
    wrote that no screen had ever named -- silence, about their own words, at
    the one moment they could still have copied them out.
    """
    sql = sql_script(ONE_APPEND_ELSEWHERE)
    app, client, session = walk(sql)
    (model,) = session.view().change.models
    assert (
        client.post("/describe", data={"model": model.name, "text": "Daily totals."}).status_code
        == 302
    )
    (question,) = session.view().questions
    assert client.post("/answer", data={"key": question.key, "kind": "append"}).status_code == 302
    try:
        sql.write_text(ONE_MERGE, encoding="utf-8")

        answers_screen = read(client.get("/").get_data(as_text=True))
        assert question.key in answers_screen.engine
        assert client.post("/stale").status_code == 302
        assert session.answers == {}
        # The description is still held, because nothing has named it yet.
        assert session.descriptions != {}

        descriptions_screen = client.get("/")
        assert descriptions_screen.status_code == 409
        named = read(descriptions_screen.get_data(as_text=True))
        assert model.name in named.engine, "the description is dropped without being named"
        assert client.post("/stale").status_code == 302

        assert session.descriptions == {}
        assert client.get("/").status_code == 200
    finally:
        sql.write_text(ONE_APPEND_ELSEWHERE, encoding="utf-8")


def test_the_describe_screen_asks_about_every_model_and_not_only_the_blank_ones(
    walk: Walk, walk_sql: Path
) -> None:
    """A row does not leave the page when it is filled.

    The mutation this is here for lists only the models with no description
    yet, which reads as progress and takes away the only place a reader can
    correct what they wrote.
    """
    app, client, session = walk(walk_sql)
    names = [model.name for model in session.view().change.models]
    saved = client.post("/describe", data={"model": names[0], "text": "One row per event."})
    assert saved.status_code == 302

    page = read(client.get("/describe").get_data(as_text=True))

    assert sum(1 for item in page.items if item == "model") == len(names)
    assert names[0] in page.engine


def test_a_model_built_from_another_says_so_where_it_is_being_described(
    walk: Walk, sql_script
) -> None:
    """Which models this one is built from, beside the box asking what it is
    for.

    A reader cannot say what a model is for without knowing what it reads,
    and the files screen -- where that is otherwise visible -- is two screens
    further on. The two guards below come with it: the names are the engine's
    and are claimed as such, and adding them did not put a sentence of this
    template's own on the page.
    """
    app, client, session = walk(sql_script(CHAINED_INSERTS))
    built = next(m for m in session.view().change.models if m.depends_on)

    page = read(client.get("/describe").get_data(as_text=True))

    # Counted, not looked for. A model's own name is already on this screen as
    # the heading of its own row, and `stg_revenue_events` is both the first
    # model here and the thing the second is built from -- so "the name is
    # somewhere on the page" is true with the whole line deleted, which is
    # what a mutation proved before this assertion was written this way.
    listed = sum(1 for item in page.items if item == "depends")
    assert listed == len(built.depends_on), f"{built.name} lists {listed} of what it reads"
    for name in built.depends_on:
        assert name in page.engine, f"{built.name} does not claim {name} from the engine"
    produced = engine_strings(session, Path("/nowhere"))
    assert not [run for run in page.engine if normalised(run) not in produced]
    assert not page.sentences(5)


def test_the_screen_refuses_a_description_dbt_could_not_render(walk: Walk, walk_sql: Path) -> None:
    """The refusal a reader is most likely to meet by accident, on the screen
    they typed it on.

    dbt renders every schema .yml through Jinja before reading it, so an
    unclosed tag in a description stops `dbt parse` for the reader's whole
    project -- in a file they never hand-wrote. Refused where they can still
    see what they typed, in the engine's own words.
    """
    app, client, session = walk(walk_sql)
    name = session.view().change.models[0].name

    refused = client.post(
        "/describe", data={"model": name, "text": "Events {% if fresh %} as of today"}
    )

    assert refused.status_code == 400
    assert session.descriptions == {}
    page = read(refused.get_data(as_text=True))
    assert any(name in run for run in page.engine), "the refusal does not name the model"


def test_a_description_is_validated_against_the_run_that_applies_the_answers(
    sql_file: Path, project_dir: Path, pipeline_runs: list[Path]
) -> None:
    """Two pipeline runs, and the second is the one that matters.

    The model names a description is checked against belong to the conversion
    the reader is looking at -- the one with their answers applied -- and the
    answers are only resolvable against the pristine run. A `describe` that
    validated against a run built with no answers would be checking names
    nobody is looking at, and its docstring would be describing a second
    implementation. Counted, because the claim is about how many conversions
    happen and nothing else can see that.
    """
    session = Session(project=project_dir, sql=sql_file)
    (question,) = session.view().questions
    session.answer(question.key, "merge_checked", ("order_id",))
    name = _model(session)
    pipeline_runs.clear()

    session.describe(name, "One row per revenue event.")

    assert len(pipeline_runs) == 2


def test_padding_round_a_description_is_not_part_of_it(sql_file: Path, project_dir: Path) -> None:
    """What lands in the .yml is the sentence, not the newline a textarea
    added to it. The box shows back what the engine holds, so the two have to
    be the same string.
    """
    session = Session(project=project_dir, sql=sql_file)
    name = _model(session)

    session.describe(name, "\n  One row per revenue event.  \n")

    (described,) = session.view().change.descriptions
    assert described.text == "One row per revenue event."
