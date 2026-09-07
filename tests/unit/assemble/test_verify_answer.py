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
from dbtw.core.passes.types import (
    Answer,
    SchemaTest,
    answer_for,
    append_option,
    merge_option,
    verify_option,
)

APPEND_SQL = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
MERGE_SQL = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n"
)
TWO_APPENDS = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
    "INSERT INTO page_views SELECT order_id, ts FROM stg_views;\n"
)
# A model that really does select both columns of a two-column key, so a
# two-column checked answer reaches the application instead of being turned
# back by the body check first.
TWO_KEY_APPEND = "INSERT INTO order_lines SELECT order_id, line_no, amount FROM stg_lines;\n"
STAR_APPEND = "INSERT INTO revenue_events SELECT * FROM stg_orders;\n"


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
    """Both columns are ones the model selects, so the body check has no
    reason to turn the answer back and the refusal has to come from the
    one-column rule itself. A two-column key whose second column the model
    does not project is declined earlier for an unrelated reason and proves
    nothing about this rule -- and hid a crash on the path that matters,
    where a test-declaring answer resolved to two applicable keys and reached
    an application that can only build a single-column check.
    """
    with pytest.raises(UnknownAnswerError, match="one column"):
        convert(
            APPEND_SQL,
            answers=answers_for_append(Answer(verify_option().label, ("order_id", "amount"))),
        )


def test_a_two_column_checked_answer_a_model_fully_selects_is_refused_not_crashed():
    """The same rule at the input that reaches furthest: every key column is
    projected, so nothing declines it before the merge is applied. The refusal
    is the documented one for a bad answer -- UnknownAnswerError -- and not an
    AssertionError or an IndexError from an application that has no
    single-column check to build.
    """
    key = _key_for(TWO_KEY_APPEND, "order_lines")
    with pytest.raises(UnknownAnswerError, match="one column"):
        convert(
            TWO_KEY_APPEND, answers={key: Answer(verify_option().label, ("order_id", "line_no"))}
        )


def test_the_one_column_refusal_says_why_dbt_can_only_check_one():
    """A caller told only "one column" cannot tell whether this engine is
    being fussy or dbt is. The reason is dbt's: its built-in test takes a
    single column, and declaring it per column on a two-column key asserts
    something stronger than the key claim."""
    key = _key_for(TWO_KEY_APPEND, "order_lines")
    with pytest.raises(UnknownAnswerError) as refused:
        convert(
            TWO_KEY_APPEND, answers={key: Answer(verify_option().label, ("order_id", "line_no"))}
        )
    message = str(refused.value)
    assert "unique" in message  # the test it declares, named
    assert "checks one column" in message
    assert "order_id, line_no" in message  # what was sent, so the caller can fix it
    # And the plain merge answer with the same two columns is untouched by the
    # rule: only the option that declares a test is bounded by what dbt can
    # check.
    merged = convert(
        TWO_KEY_APPEND, answers={key: Answer(merge_option().label, ("order_id", "line_no"))}
    )
    assert _model(merged, "order_lines").unique_key == ("order_id", "line_no")
    assert merged.tests == ()


def test_a_checked_answer_with_two_columns_one_unprojected_is_still_refused():
    """The other two-column shape: the body check would decline this key
    anyway, and the answer still has to be refused for what it asked for
    rather than silently landing as a keyless append."""
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
    # The decline is recorded, not silent -- the same Decision the plain merge
    # answer gets, since the body check does not care which answer asked.
    (declined,) = [
        d for d in change.decisions if d.key == "assemble.unique_key_not_selected.revenue_events"
    ]
    assert "customer_id" in declined.action


def test_answering_merge_leaves_the_check_on_offer_for_the_next_run():
    """The web loop's actual sequence: a question is answered, the rewritten
    Decision is what the next screen renders, and the answer not taken has to
    still be there to take. An option that disappears once any answer is
    applied can only ever be chosen by a caller who never saw the first
    result.

    Two separate claims, and the second is narrower than it looks. The
    rewritten question still shows the checked answer; and a second run that
    asks for the check is accepted and records the test. That second run
    sends the *keyless* label with its column -- the vocabulary the question
    `run_passes` hands out uses, which is what every run validates against.
    Sending back the keyed label this rewritten Decision displays is refused
    today; that boundary is pinned directly below rather than papered over
    here.
    """
    key = _key_for(APPEND_SQL, "revenue_events")
    merged = convert(APPEND_SQL, answers={key: Answer(merge_option().label, ("order_id",))})

    rewritten = _question_for(merged, "revenue_events")
    assert rewritten.chosen == merge_option(("order_id",)).label
    assert verify_option(("order_id",)).label in [o.label for o in rewritten.options]

    checked = convert(APPEND_SQL, answers={key: Answer(verify_option().label, ("order_id",))})
    assert checked.models == merged.models
    assert len(checked.tests) == 1


def test_re_sending_an_append_questions_rewritten_checked_label_is_refused_today():
    """A known boundary, pinned so it is a documented edge rather than a
    surprise. An append question is asked in keyless vocabulary ("merge on a
    unique key, checked on every run" plus a column); once answered, the
    rewritten Decision names the key in its labels ("merge on order_id,
    checked on every run"). Every run validates answers against the question
    the passes hand out, which is always the keyless one -- so a caller that
    echoes back the label it was just shown is refused.

    Not a defect this test hides: the refusal is explicit and names what the
    question does offer. It is a translation the session in front of these
    runs has to do -- keep the keyed label for display, send the keyless label
    and the column back -- and it is no longer done by hand: `Option.kind` and
    `answer_for` are that translation, and the round trip is the test directly
    below. The refusal itself stays, because the engine still cannot know that
    two spellings mean one answer. The merge question has no such gap, because
    its labels name the key from the start (see the downgrade test below,
    where the same echo is accepted).
    """
    key = _key_for(APPEND_SQL, "revenue_events")
    merged = convert(APPEND_SQL, answers={key: Answer(merge_option().label, ("order_id",))})
    (displayed,) = [
        o.label
        for o in _question_for(merged, "revenue_events").options
        if o.label == verify_option(("order_id",)).label
    ]

    # Matched on the message: the same call raises for a stale key too, and
    # this refusal has to be the one that says the label is not on offer.
    with pytest.raises(UnknownAnswerError, match="does not offer"):
        convert(APPEND_SQL, answers={key: Answer(displayed)})
    with pytest.raises(UnknownAnswerError, match="does not offer"):
        convert(APPEND_SQL, answers={key: Answer(displayed, ("order_id",))})

    # The translation that does work, so the boundary comes with its way out.
    translated = convert(APPEND_SQL, answers={key: Answer(verify_option().label, ("order_id",))})
    assert len(translated.tests) == 1


def test_answer_for_is_how_a_caller_re_sends_an_answer_across_a_rebuild():
    """The refusal above is still right -- the engine cannot know that two
    spellings mean one answer. `answer_for` is where that knowledge lives,
    and this is the round trip the web loop makes on every answer after the
    first.

    What crosses between the two runs is `Option.kind`, read as a field off
    the Decision the screen rendered, and resolved back into a label against
    the pristine question every run validates answers against. The keyed
    spelling the screen displayed is never carried anywhere: the caller sends
    the wording the question it is answering actually offers, which is the
    translation the docstring above describes and no longer has to be done by
    hand.
    """
    key = _key_for(APPEND_SQL, "revenue_events")
    pristine = _question_for(convert(APPEND_SQL), "revenue_events")

    first = convert(APPEND_SQL, answers={key: answer_for(pristine, "merge_checked", ("order_id",))})
    assert len(first.tests) == 1

    # The next screen renders the rewritten question, and the user takes the
    # checked answer off it again. What a consumer records is the kind.
    rebuilt = _question_for(first, "revenue_events")
    (picked,) = [o for o in rebuilt.options if o.label == verify_option(("order_id",)).label]
    assert picked.kind == "merge_checked"
    # `answer_for` is faithful to whichever Decision it is handed -- against
    # the rebuilt one it returns the keyed label, which is exactly the answer
    # the run above refuses. So the caller resolves against the pristine
    # question, and gets the keyless spelling back for the same kind.
    assert answer_for(rebuilt, picked.kind).label == picked.label
    resent = answer_for(pristine, picked.kind, ("order_id",))
    assert resent.label != picked.label
    assert resent == Answer(verify_option().label, ("order_id",))

    second = convert(APPEND_SQL, answers={key: resent})
    assert second.models == first.models
    assert second.tests == first.tests


def test_a_checked_answer_on_a_star_model_merges_with_the_caveat_and_records_the_test():
    """SELECT * is the case the body check cannot settle: the key may or may
    not be projected, and the merge is applied with a caveat saying so rather
    than blocked or confidently claimed. The test is recorded alongside it --
    which is the honest outcome for a feature whose whole point is asking dbt
    to check the claim this conversion could not, but it is behaviour nobody
    would guess from the code, so it is pinned here.
    """
    key = _key_for(STAR_APPEND, "revenue_events")
    change = convert(STAR_APPEND, answers={key: Answer(verify_option().label, ("order_id",))})

    model = _model(change, "revenue_events")
    assert (model.incremental_strategy, model.unique_key) == ("merge", ("order_id",))
    (test,) = change.tests
    assert test == SchemaTest(model=model.name, column="order_id", test="unique")
    dec = _question_for(change, "revenue_events")
    assert "could not be verified" in dec.reason
    assert dec.chosen == verify_option(("order_id",)).label


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

    # And coming back to the checked answer on the next run is accepted --
    # here the label the rewritten Decision displays and the label the passes
    # offer are the same string, because a MERGE's key is known from the
    # start. This is the case the append question cannot reach; see the
    # boundary test above.
    checked = convert(MERGE_SQL, answers={key: Answer(verify_option(("id",)).label)})
    assert len(checked.tests) == 1
