import re

import pytest

from dbtw.core.passes.types import (
    append_option,
    inline_option,
    merge_option,
    var_option,
    verify_option,
)

# Terms the persona walkthroughs recorded as undefined for people who have
# never used dbt (spec section 11.3). The plain register exists to say the
# same thing without them, so its wording must contain none of these.
JARGON = (
    "incremental",
    "materialized",
    "materialization",
    "unique_key",
    "ref(",
    "source(",
    "jinja",
    "dbt run",
    "dbt build",
    "dbt test",
    "dbt compile",
    "warehouse",
    "staging",
    "config(",
    "{{",
)

ALL_OPTIONS = [
    append_option(),
    merge_option(),
    merge_option(("order_id",)),
    verify_option(),
    verify_option(("order_id",)),
    inline_option(),
    var_option("cutoff"),
]


def _jargon_in(text: str) -> list[str]:
    lowered = text.lower()
    return [term for term in JARGON if term in lowered]


@pytest.mark.parametrize("option", ALL_OPTIONS, ids=lambda o: o.label)
def test_every_option_carries_a_plain_wording(option):
    assert option.plain.strip(), f"{option.label} has no plain wording"
    assert option.plain != option.effect


@pytest.mark.parametrize("option", ALL_OPTIONS, ids=lambda o: o.label)
def test_the_plain_wording_uses_no_dbt_vocabulary(option):
    found = _jargon_in(option.plain)
    assert not found, f"{option.label}'s plain wording uses {found}"


def test_the_dbt_native_effect_is_still_allowed_its_vocabulary():
    """The registers are for different readers. This asserts they really are
    different -- if `effect` ever stops naming dbt's own mechanics, the plain
    register has quietly replaced it instead of sitting beside it."""
    assert "unique_key" in merge_option().effect


def test_the_plain_append_wording_names_the_consequence_a_newcomer_cares_about():
    """Not a tautology check: this fails if the wording is softened to
    something that no longer says what goes wrong. Lowercased so a rewording
    that opens a sentence with the word ("Twice...") doesn't fail spuriously
    on casing alone."""
    plain = append_option().plain.lower()
    assert "twice" in plain or "duplicate" in plain


def test_the_plain_wording_check_catches_an_interpolated_banned_term():
    """`merge_option` and `var_option` splice a caller-supplied identifier
    straight into `plain`. The jargon check above only ever runs against the
    pinned ALL_OPTIONS list, where those identifiers ('order_id', 'cutoff')
    happen to be clean -- so it has never actually been proven to catch a
    banned term arriving through the splice rather than the fixed prose.
    This constructs the case where the splice itself is the violation and
    confirms the same check-logic used elsewhere flags it."""
    option = merge_option(("unique_key",))
    found = _jargon_in(option.plain)
    assert found == ["unique_key"], f"expected the check to catch 'unique_key', got {found}"


def _assert_plain_forms(d) -> None:
    """The invariant shared by every question in a real conversion: a plain
    question, no jargon in it, every option has non-blank plain wording with
    no jargon, and no two options for the same question share one plain
    string (an unremarkable-looking failure mode: `inline_option()` and
    `var_option()` are opposite answers, and a copy-paste between them would
    make every assertion above still pass individually)."""
    assert d.plain_question.strip(), f"{d.key} has no plain question"
    found = _jargon_in(d.plain_question)
    assert not found, f"{d.key}: {d.plain_question}"
    plains = []
    for option in d.options:
        assert option.plain.strip(), f"{d.key} offers {option.label} with no plain wording"
        found = _jargon_in(option.plain)
        assert not found, f"{d.key} offers {option.label} with jargon in plain: {found}"
        plains.append(option.plain)
    assert len(set(plains)) == len(plains), f"{d.key} offers options with duplicate plain wording"


def test_every_question_and_option_in_a_real_conversion_has_a_plain_form():
    from dbtw.core.assemble import assemble
    from dbtw.core.context import read_project
    from dbtw.core.ingest import classify_statements, ingest
    from dbtw.core.passes import run_passes

    ir = ingest("tests/fixtures/sql/incremental_etl.sql", None)
    change = assemble(
        run_passes(classify_statements(ir), ir.dialect),
        read_project("tests/fixtures/projects/jaffle_shop"),
    )
    questions = [d for d in change.decisions if d.question]
    assert questions, "fixture stopped producing questions; pick another"
    for d in questions:
        _assert_plain_forms(d)


def test_the_variable_question_in_a_real_conversion_has_a_plain_form_too():
    """The invariant above never asks a variable question -- its fixture
    (incremental_etl.sql) has no DECLARE, so it only ever exercises the merge
    and append sites. Same gap Task 1 hit for `subject`; see
    tests/unit/passes/test_subject.py's
    test_a_variable_question_in_a_real_conversion_carries_a_subject_too,
    which this mirrors. Inlines the same DECLARE-and-append script as that
    test and as test_answers.py's VARIABLE_AND_APPEND, rather than inventing
    a new fixture."""
    from tests.unit.assemble.helpers import convert

    variable_and_append = (
        "DECLARE @cutoff DATE = '2024-01-01';\n"
        "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders "
        "WHERE order_date >= @cutoff;\n"
    )
    change = convert(variable_and_append, dialect="tsql")
    questions = [d for d in change.decisions if d.question]
    assert questions, "fixture stopped producing questions; pick another"
    for d in questions:
        _assert_plain_forms(d)
    (variable_q,) = [d for d in questions if d.question.startswith("Is ")]
    assert variable_q.plain_question


# --- the append question's own sentence, against the model written beside it

APPEND_SCRIPT = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"

# The target named as a destination: rows are *added to* it. The wording this
# replaced -- "add every row it finds in revenue_events" -- names the same
# table as the place rows are read from, and fails this.
_NAMED_AS_DESTINATION = re.compile(
    r"\b(add|adds|added|append|appended|insert|inserted|write|written)\w*\s+to\s+revenue_events\b"
)


def _append_question():
    """The append question as a real conversion produces it, with the model
    built from the same statement. Read off the pipeline rather than rebuilt
    here, so these assert the sentence a reader is actually shown."""
    from tests.unit.assemble.helpers import convert

    change = convert(APPEND_SCRIPT)
    (question,) = [d for d in change.decisions if d.question and d.chosen == "append every row"]
    (model,) = change.models
    return model, question


def test_the_plain_append_question_names_its_target_as_where_rows_go():
    """The table in this question is the INSERT's destination, and the model
    written beside the question proves it: the body reads stg_orders and does
    not mention revenue_events anywhere. A sentence saying rows are found in
    revenue_events describes a file this conversion did not write -- and the
    plain register is the one register whose reader cannot correct for it."""
    model, question = _append_question()
    plain = question.plain_question

    # Not vacuous: this is the contradiction the sentence must not create.
    assert "stg_orders" in model.body
    assert "revenue_events" not in model.body

    assert "revenue_events" in plain, plain
    assert _NAMED_AS_DESTINATION.search(plain), plain


def test_the_plain_append_question_offers_the_answer_the_merge_option_describes():
    """The question's second branch is `merge_option()`. Its plain wording,
    rendered directly below the question, says a match updates the existing
    row; the worked example below that shows the update happening. A question
    offering that branch as "only the ones it has not seen before" describes
    an insert-only skip -- a third behaviour, which neither the option nor the
    example nor dbt produces."""
    _, question = _append_question()
    plain = question.plain_question.lower()
    # Not `startswith("merge")`: the append question now also offers
    # `verify_option()`, whose label ("merge on a unique key, checked on
    # every run") starts with the same word. This test is about the plain
    # merge answer specifically, so it is matched on its exact label.
    (merge,) = [o for o in question.options if o.label == "merge on a unique key"]

    # Not vacuous: the alternative really is match-and-update, so the question
    # has to offer it as one. If merge_option stops updating, this fails here
    # rather than leaving the question quietly describing the wrong thing.
    assert "update" in merge.plain.lower(), merge.plain

    assert "match" in plain, plain
    assert "update" in plain, plain
