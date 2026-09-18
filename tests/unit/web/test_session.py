"""One conversation's conversion, held by a Session.

The session's whole job is to keep answers usable across runs. Every test
here is about one of the three ways that breaks: recording prose the engine
rewrites, resolving an answer against the run that was displayed rather than
the run the engine validates against, and letting the source file move so the
keys stop matching.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.web.conftest import ONE_MERGE, VARIABLE_AND_APPEND

from dbtw.core.assemble import UnknownAnswerError
from dbtw.core.passes import SchemaTest
from dbtw.web.state import Session


def test_an_answer_can_be_given_again_after_the_engine_rewrote_the_labels(
    project_dir: Path, sql_file: Path
) -> None:
    """The label a Decision offers changes once an answer is applied: the
    pristine question offers "merge on a unique key, checked on every run"
    and the Decision rebuilt from that answer offers "merge on order_id,
    checked on every run" for the same choice. A session that recorded the
    label, or that resolved a kind against the Decision it displayed rather
    than the pristine one, refuses every answer after the first.
    """
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers={})
    (question,) = session.questions()
    pristine_labels = {option.kind: option.label for option in question.options}

    session.answer(question.key, "merge_checked", ("order_id",))
    first = session.current()

    # The answer reached the model, so "the second answer matched the first"
    # below is not two runs of an ignored answer agreeing with each other.
    (model,) = first.models
    assert model.incremental_strategy == "merge"
    assert model.unique_key == ("order_id",)
    assert first.tests == (SchemaTest(model=model.name, column="order_id", test="unique"),)

    (rebuilt,) = [d for d in first.decisions if d.key == question.key]
    rebuilt_labels = {option.kind: option.label for option in rebuilt.options}
    # The trap: one answer, two spellings, and the rebuilt one is what a
    # screen has in front of it when the user answers a second time.
    assert rebuilt_labels["merge_checked"] != pristine_labels["merge_checked"]
    assert rebuilt.chosen == rebuilt_labels["merge_checked"]

    (kind,) = [option.kind for option in rebuilt.options if option.label == rebuilt.chosen]
    session.answer(question.key, kind, ("order_id",))
    second = session.current()

    assert second.models == first.models
    assert second.tests == first.tests
    assert session.answers == {question.key: ("merge_checked", ("order_id",))}


def test_the_question_keys_do_not_move_between_runs(project_dir: Path, sql_file: Path) -> None:
    """Tier-2 keys embed the source file's path (spec 11.7). A session that
    copied its input somewhere fresh per run would hand out a key the next
    run has never heard of, and every answer held against it is refused.
    """
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers={})

    # Exact, not "contains the path": the key has to name the file the user
    # gave, at the index the pipeline read it at.
    expected = f"tier2.append.{sql_file}:0"
    assert {d.key for d in session.questions()} == {expected}
    assert {d.key for d in session.questions()} == {expected}

    session.answer(expected, "append", ())
    assert {d.key for d in session.questions()} == {expected}


def test_an_unknown_key_is_refused_and_leaves_the_session_untouched(
    project_dir: Path, sql_file: Path
) -> None:
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers={})
    with pytest.raises(UnknownAnswerError, match="no question with key") as excinfo:
        session.answer("tier2.append.nope.sql:99", "append", ())

    message = str(excinfo.value)
    assert "tier2.append.nope.sql:99" in message
    # Diagnosable: the refusal names what this conversion does ask.
    assert f"tier2.append.{sql_file}:0" in message

    assert session.answers == {}
    assert len(session.questions()) == 1


def test_a_kind_the_question_does_not_offer_is_refused(project_dir: Path, sql_file: Path) -> None:
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers={})
    (question,) = session.questions()

    with pytest.raises(ValueError, match="offers no 'var' option") as excinfo:
        session.answer(question.key, "var", ())

    assert "append, merge, merge_checked" in str(excinfo.value)
    assert session.answers == {}


def test_a_decision_that_asks_nothing_cannot_be_answered(project_dir: Path, sql_file: Path) -> None:
    """Spec 3.3: a Tier-2 Decision is not necessarily a question. The rewrite
    Decision is tier 2 and carries a `chosen`, and a session keying on either
    of those would accept an answer to something nobody was asked.
    """
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers={})
    (rewrite,) = [d for d in session.current().decisions if d.key.startswith("assemble.rewrite.")]
    assert rewrite.tier == 2
    assert rewrite.chosen
    assert not rewrite.question

    with pytest.raises(UnknownAnswerError, match="asks no question") as excinfo:
        session.answer(rewrite.key, "append", ())

    assert rewrite.key in str(excinfo.value)
    assert session.answers == {}


def test_an_answer_the_assembler_refuses_is_not_recorded(project_dir: Path, sql_file: Path) -> None:
    """`answer_for` accepts two columns for a checked merge -- the option asks
    for columns and got some -- and the assembler is where the one-column
    limit of dbt's unique test is enforced. If the session recorded the
    answer and left the refusal to the next render, one bad answer would make
    every later call raise and the conversation would be over.
    """
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers={})
    (question,) = session.questions()

    with pytest.raises(UnknownAnswerError, match="only be answered with one column"):
        session.answer(question.key, "merge_checked", ("order_id", "amount"))

    assert session.answers == {}
    still = session.current()
    (model,) = still.models
    assert model.incremental_strategy == "append"
    assert model.unique_key == ()
    assert still.tests == ()


def test_two_questions_of_different_families_are_answered_independently(
    project_dir: Path, sql_script: Callable[[str], Path]
) -> None:
    """One key comes from a tier-2 pass and embeds the source path, the other
    from assemble and does not. A session that only looked up one family
    could not answer the other at all.
    """
    sql = sql_script(VARIABLE_AND_APPEND)
    session = Session(project=project_dir, sql=sql, dialect="tsql", answers={})

    # Pipeline order, which is the order the walk asks them in.
    assert [d.key for d in session.questions()] == [
        f"tier2.append.{sql}:1",
        "assemble.variable.cutoff",
    ]
    append_key, variable_key = (d.key for d in session.questions())

    session.answer(variable_key, "inline", ())
    session.answer(append_key, "merge", ("order_id",))

    (model,) = session.current().models
    assert model.unique_key == ("order_id",)
    assert "'2024-01-01'" in model.body
    assert "var('cutoff')" not in model.body
    assert session.answers == {
        variable_key: ("inline", ()),
        append_key: ("merge", ("order_id",)),
    }


def test_an_option_that_names_its_own_key_refuses_columns(
    project_dir: Path, sql_script: Callable[[str], Path]
) -> None:
    """A MERGE's ON clause already named the key, so this question's options
    spell it and take no columns -- the mirror image of an append question,
    whose merge options are keyless and require them. Whether columns travel
    with an answer is the option's business, not the caller's.
    """
    sql = sql_script(ONE_MERGE)
    session = Session(project=project_dir, sql=sql, dialect=None, answers={})
    (question,) = session.questions()
    assert [option.kind for option in question.options] == ["merge", "append", "merge_checked"]
    assert [option.columns_prompt for option in question.options] == ["", "", ""]

    with pytest.raises(ValueError, match="names its own key") as excinfo:
        session.answer(question.key, "merge", ("id",))
    assert "('id',)" in str(excinfo.value)
    assert session.answers == {}

    session.answer(question.key, "merge_checked", ())
    change = session.current()
    (model,) = change.models
    assert model.unique_key == ("id",)
    assert change.tests == (SchemaTest(model=model.name, column="id", test="unique"),)


def test_the_columns_an_answer_needs_are_read_off_the_pristine_question(
    project_dir: Path, sql_file: Path
) -> None:
    """The requirement a screen can see and the requirement an answer must
    meet are not the same after the first answer: the rebuilt options name
    their key and ask for nothing, while the answer still goes to the
    pristine question, which asks. `columns_prompts` is the only honest
    source for a picker, and this is why the session exposes one.
    """
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers={})
    (question,) = session.questions()
    key_prompt = "the column(s) that identify a row uniquely"
    assert session.columns_prompts(question.key) == {
        "append": "",
        "merge": key_prompt,
        "merge_checked": key_prompt,
    }

    session.answer(question.key, "merge_checked", ("order_id",))
    (rebuilt,) = [d for d in session.current().decisions if d.key == question.key]

    # What a screen rendering the rebuilt Decision would conclude ...
    assert [option.columns_prompt for option in rebuilt.options] == ["", "", ""]
    # ... and what the answer actually still needs.
    assert session.columns_prompts(question.key)["merge_checked"] == key_prompt
    with pytest.raises(ValueError, match="got no columns"):
        session.answer(question.key, "merge_checked", ())


def test_questions_are_the_decisions_that_ask_something(project_dir: Path, sql_file: Path) -> None:
    """Spec 3.3: key on `question`, never on `tier` or on the presence of
    `chosen`. This conversion carries a Decision that would be picked up by
    either of the wrong two.
    """
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers={})
    change = session.current()

    assert [d.key for d in session.questions()] == [f"tier2.append.{sql_file}:0"]

    silent = [d for d in change.decisions if not d.question]
    assert any(d.tier == 2 for d in silent)
    assert any(d.chosen for d in silent)


def test_the_session_holds_paths_rather_than_a_snapshot_of_one_run(
    project_dir: Path, sql_script: Callable[[str], Path]
) -> None:
    """Spec 4.3: the answer loop re-runs the pipeline. A session that cached
    a run would go on describing a script the file no longer contains -- and
    would hold out keys nothing can be answered against.

    The second script is a MERGE so that both the run a screen renders and
    the pristine run an answer is resolved against have to have moved: a
    cache on either one alone leaves one of these assertions wrong.
    """
    sql = sql_script("INSERT INTO daily_totals SELECT order_id, amount FROM stg_orders;\n")
    session = Session(project=project_dir, sql=sql, dialect=None, answers={})
    assert [model.name for model in session.current().models] == ["stg_daily_totals"]
    assert [d.key for d in session.questions()] == [f"tier2.append.{sql}:0"]

    sql.write_text(ONE_MERGE, encoding="utf-8")

    assert [model.name for model in session.current().models] == ["stg_dim_c"]
    assert [d.key for d in session.questions()] == [f"tier2.merge.{sql}:0"]
    assert session.columns_prompts(f"tier2.merge.{sql}:0") == {
        "merge": "",
        "append": "",
        "merge_checked": "",
    }


def test_a_session_given_answers_up_front_applies_them(project_dir: Path, sql_file: Path) -> None:
    """`answers` is an ordinary field, so a caller can build a session that
    already holds some -- the shape a later slice needs to reload one. They
    go through the same resolution as an answer given by `answer`.
    """
    key = f"tier2.append.{sql_file}:0"
    session = Session(
        project=project_dir, sql=sql_file, dialect=None, answers={key: ("merge", ("order_id",))}
    )
    (model,) = session.current().models
    assert model.incremental_strategy == "merge"
    assert model.unique_key == ("order_id",)


def test_the_answers_dict_the_caller_passed_in_is_the_one_that_is_updated(
    project_dir: Path, sql_file: Path
) -> None:
    """A caller holding the dict it handed over sees what the session
    recorded, and sees nothing after a refusal.
    """
    held: dict[str, tuple[str, tuple[str, ...]]] = {}
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers=held)
    (question,) = session.questions()

    session.answer(question.key, "merge", ("order_id",))
    assert held == {question.key: ("merge", ("order_id",))}

    with pytest.raises(UnknownAnswerError, match="only be answered with one column"):
        session.answer(question.key, "merge_checked", ("order_id", "amount"))
    # The answer that already stood is still there, unaltered by the refusal.
    assert held == {question.key: ("merge", ("order_id",))}


def test_a_conversion_with_nothing_to_answer_is_not_an_error(
    project_dir: Path, sql_script: Callable[[str], Path]
) -> None:
    """Spec 7's "nothing to answer" row. A clean script is the common case
    and must come back as a conversion with no questions, not as a failure.
    """
    sql = sql_script("SELECT id, name INTO dim_people FROM raw_people;\n")
    session = Session(project=project_dir, sql=sql, dialect="tsql", answers={})
    change = session.current()
    assert session.questions() == ()
    assert [model.name for model in change.models] == ["stg_dim_people"]


def test_two_sessions_do_not_share_an_answers_dict(project_dir: Path, sql_file: Path) -> None:
    one = Session(project=project_dir, sql=sql_file, dialect=None)
    other = Session(project=project_dir, sql=sql_file, dialect=None)
    (question,) = one.questions()
    one.answer(question.key, "append", ())
    assert one.answers == {question.key: ("append", ())}
    assert other.answers == {}


def test_the_unused_append_answer_is_still_the_conversion_as_it_stood(
    project_dir: Path, sql_file: Path
) -> None:
    """Answering "append every row" chooses what the engine already proposed.
    It must be recorded all the same -- a user who picked it made a decision,
    and a session that dropped it would show an unanswered question again.
    """
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers={})
    (question,) = session.questions()
    before = session.current()

    session.answer(question.key, "append", ())
    after = session.current()

    assert session.answers == {question.key: ("append", ())}
    assert after.models == before.models
    (rebuilt,) = [d for d in after.decisions if d.key == question.key]
    assert rebuilt.chosen == "append every row"


def test_the_sql_and_the_project_are_read_where_the_session_was_pointed(
    project_dir: Path, sql_file: Path
) -> None:
    """The project is read per run too, so a session is a live view of two
    paths rather than a copy of either.
    """
    session = Session(project=project_dir, sql=sql_file, dialect=None, answers={})
    assert session.current().project_name == "jaffle_shop"

    (project_dir / "dbt_project.yml").write_text(
        "name: renamed_shop\nversion: '1.0'\nprofile: jaffle_shop\n", encoding="utf-8"
    )
    assert session.current().project_name == "renamed_shop"


def test_a_dialect_is_carried_into_every_run(
    project_dir: Path, sql_script: Callable[[str], Path]
) -> None:
    """The DECLARE only parses as T-SQL. A session that dropped the dialect
    would classify it as unsupported and never ask the variable's question.
    """
    sql = sql_script(VARIABLE_AND_APPEND)
    with_dialect = Session(project=project_dir, sql=sql, dialect="tsql", answers={})
    without = Session(project=project_dir, sql=sql, dialect=None, answers={})

    assert "assemble.variable.cutoff" in [d.key for d in with_dialect.questions()]
    assert "assemble.variable.cutoff" not in [d.key for d in without.questions()]
    assert with_dialect.current().dialect == "tsql"
    assert without.current().dialect is None
