"""Answering "checked on every run" applies the same merge the plain merge
answer applies, and records the dbt test that was asked for.

The check is dbt's built-in `unique` test, declared in a .yml file beside the
model. This module is about the record that says so: one `SchemaTest` per
answer that asked for one, none for any other answer, and a model file that is
byte for byte the file the plain merge answer would have written -- the two
answers differ in what is recorded, never in what is converted.
"""

import pytest
from tests.unit.assemble.helpers import convert

from dbtw.core.assemble import UnknownAnswerError
from dbtw.core.emit.render import render_model
from dbtw.core.passes.types import Answer, SchemaTest, append_option, merge_option, verify_option

APPEND_SQL = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
MERGE_SQL = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n"
)
TWO_APPENDS = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
    "INSERT INTO page_views SELECT order_id, ts FROM stg_views;\n"
)


def _question_for(change, table):
    (dec,) = [d for d in change.decisions if d.question and table in d.action]
    return dec


def _key_for(sql, table, **convert_kwargs):
    """The Decision.key an unanswered run of `sql` hands out for `table` --
    the key a caller answering the question would be holding."""
    return _question_for(convert(sql, **convert_kwargs), table).key


def answers_for_append(answer):
    return {_key_for(APPEND_SQL, "revenue_events"): answer}


def _model(change, table):
    return next(m for m in change.models if table in m.name)


def test_the_checked_answer_records_exactly_the_chosen_test_and_no_other():
    change = convert(
        APPEND_SQL, answers=answers_for_append(Answer(verify_option().label, ("order_id",)))
    )
    (test,) = change.tests
    assert test == SchemaTest(
        model=_model(change, "revenue_events").name, column="order_id", test="unique"
    )


def test_the_recorded_test_names_the_model_that_was_actually_written():
    """The .yml goes beside the model and its `models:` entry has to name a
    model that exists. This project renames `revenue_events` to
    `stg_revenue_events` on the way in, so a test recorded against the draft
    name would declare a check on a model dbt has never heard of."""
    change = convert(
        APPEND_SQL, answers=answers_for_append(Answer(verify_option().label, ("order_id",)))
    )
    (test,) = change.tests
    written = {m.name for m in change.models}
    assert test.model in written
    assert test.model == "stg_revenue_events"  # the renamed model, not the draft


def test_no_not_null_is_ever_recorded():
    """The round-2 prototype emitted a not_null test nobody chose; two personas
    caught it as an invented decision. Only what the user picked ships."""
    change = convert(
        APPEND_SQL, answers=answers_for_append(Answer(verify_option().label, ("order_id",)))
    )
    assert change.tests != ()
    assert all(t.test == "unique" for t in change.tests)


def test_the_checked_model_is_byte_identical_to_the_merge_model():
    """Single code path: verify differs from merge only in the recorded test.

    Both halves are asserted here. Without the recorded test on the checked
    side, this equality would also be satisfied by a run that ignored the
    checked answer altogether and quietly merged -- which is the regression
    the equality is here to catch the other side of.
    """
    checked = convert(
        APPEND_SQL, answers=answers_for_append(Answer(verify_option().label, ("order_id",)))
    )
    merged = convert(
        APPEND_SQL, answers=answers_for_append(Answer(merge_option().label, ("order_id",)))
    )
    assert render_model(checked.models[0]) == render_model(merged.models[0])
    assert checked.models == merged.models
    assert len(checked.tests) == 1
    assert merged.tests == ()


def test_an_unanswered_conversion_records_no_tests():
    assert convert(APPEND_SQL).tests == ()


def test_only_the_model_whose_question_asked_for_a_check_gets_one():
    """Two models, two answers, one check. A run that recorded the test
    against every incremental model, or against the wrong one of two, would
    write a .yml claiming a uniqueness nobody asserted."""
    baseline = convert(TWO_APPENDS)
    revenue = _question_for(baseline, "revenue_events").key
    views = _question_for(baseline, "page_views").key

    change = convert(
        TWO_APPENDS,
        answers={
            revenue: Answer(verify_option().label, ("order_id",)),
            views: Answer(append_option().label),
        },
    )
    (test,) = change.tests
    assert test == SchemaTest(
        model=_model(change, "revenue_events").name, column="order_id", test="unique"
    )
    assert _model(change, "page_views").incremental_strategy == "append"


def test_a_checked_answer_with_two_columns_is_refused_with_the_reason():
    with pytest.raises(UnknownAnswerError, match="one column"):
        convert(
            APPEND_SQL,
            answers=answers_for_append(Answer(verify_option().label, ("order_id", "line_no"))),
        )


def test_a_checked_answer_with_no_columns_is_refused_like_any_keyless_merge():
    """The keyless checked answer names no column, the same way "merge on a
    unique key" names no key -- and is refused by the same gate, so a caller
    that sends neither is told what is missing rather than getting a merge on
    nothing with a test on nothing."""
    with pytest.raises(UnknownAnswerError, match="with no columns"):
        convert(APPEND_SQL, answers=answers_for_append(Answer(verify_option().label)))


def test_the_decision_records_the_checked_choice():
    change = convert(
        APPEND_SQL, answers=answers_for_append(Answer(verify_option().label, ("order_id",)))
    )
    dec = _question_for(change, "revenue_events")
    assert dec.chosen == verify_option(("order_id",)).label
    # A `chosen` naming an option the rewritten question does not offer would
    # render as an answer nobody could have given.
    assert dec.chosen in [o.label for o in dec.options]


def test_a_checked_answer_at_a_merge_question_needs_no_columns():
    """At a MERGE's question the key is already known and named in the label,
    so the checked answer carries no columns -- and still applies the merge
    the script built and records the test on that key."""
    key = _key_for(MERGE_SQL, "dim_c")
    baseline = convert(MERGE_SQL)
    change = convert(MERGE_SQL, answers={key: Answer(verify_option(("id",)).label)})

    assert change.models == baseline.models  # the merge the script already built
    (test,) = change.tests
    assert test == SchemaTest(model=_model(change, "dim_c").name, column="id", test="unique")
    dec = _question_for(change, "dim_c")
    assert dec.chosen == verify_option(("id",)).label
    assert dec.chosen in [o.label for o in dec.options]


def test_columns_sent_with_a_merge_questions_checked_answer_are_refused():
    """The label already names the key, so columns beside it would be
    discarded -- leaving the caller believing a different key was recorded."""
    key = _key_for(MERGE_SQL, "dim_c")
    with pytest.raises(UnknownAnswerError, match="takes no columns"):
        convert(MERGE_SQL, answers={key: Answer(verify_option(("id",)).label, ("id",))})


def test_a_checked_answer_naming_a_column_the_model_does_not_select_records_no_test():
    """The body check declines a key the model never projects, whoever asked
    for it. A test recorded anyway would declare a dbt check on a column the
    model does not have -- and would claim a check beside a model that was
    left an append, which is not what the report says happened."""
    change = convert(
        APPEND_SQL, answers=answers_for_append(Answer(verify_option().label, ("customer_id",)))
    )
    (model,) = change.models
    assert (model.incremental_strategy, model.unique_key) == ("append", ())
    assert change.tests == ()


def test_answering_merge_leaves_the_check_on_offer_for_the_next_run():
    """The web loop's actual sequence: a question is answered, the rewritten
    Decision is what the next screen renders, and the answer not taken has to
    still be there to take. An option that disappears once any answer is
    applied can only ever be chosen by a caller who never saw the first
    result -- and the run that re-sends it must be accepted, not refused.
    """
    key = _key_for(APPEND_SQL, "revenue_events")
    merged = convert(APPEND_SQL, answers={key: Answer(merge_option().label, ("order_id",))})

    rewritten = _question_for(merged, "revenue_events")
    assert rewritten.chosen == merge_option(("order_id",)).label
    assert verify_option(("order_id",)).label in [o.label for o in rewritten.options]

    checked = convert(APPEND_SQL, answers={key: Answer(verify_option().label, ("order_id",))})
    assert checked.models == merged.models
    assert len(checked.tests) == 1


def test_answering_append_on_a_merge_leaves_the_check_on_offer_too():
    """The mirror at the downgrade: a MERGE answered "append every row" keeps
    both of the answers it turned down -- the merge and the checked merge --
    so the user can come back to either."""
    key = _key_for(MERGE_SQL, "dim_c")
    downgraded_change = convert(MERGE_SQL, answers={key: Answer(append_option().label)})
    downgraded = _question_for(downgraded_change, "dim_c")
    assert downgraded_change.tests == ()
    assert [o.label for o in downgraded.options] == [
        append_option().label,
        merge_option(("id",)).label,
        verify_option(("id",)).label,
    ]

    # And coming back to the checked answer on the next run is accepted.
    checked = convert(MERGE_SQL, answers={key: Answer(verify_option(("id",)).label)})
    assert len(checked.tests) == 1
