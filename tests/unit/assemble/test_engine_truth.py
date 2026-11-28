"""Each sentence that the engine writes is true of the model that it describes.

Browser testers read the walk over the demo script (`dbtw-demo/nightly.sql`)
and found sentences that the files beside them contradict: a model that joins
two raw tables placed in staging, which the glossary says uses no joins; a
layer option that promises a table for an ephemeral model; a unique key
spelled one way in a Decision and another way in the config. Each test below
reads one of those sentences against the model that came out.
"""

from __future__ import annotations

import pytest
from tests.unit.assemble.helpers import context_for, convert

from dbtw.core.assemble import UnknownAnswerError
from dbtw.core.emit.render import render_model
from dbtw.core.passes import Answer, Decision, answer_for
from dbtw.core.passes.types import merge_option, verify_option

THREE = "three_layers"

# The demo script. revenue_events joins two raw tables, and revenue_daily
# reads revenue_events.
NIGHTLY = (
    "INSERT INTO revenue_events\n"
    "SELECT o.order_id, c.name AS customer_name, o.amount, o.order_date\n"
    "FROM analytics.orders AS o\n"
    "JOIN analytics.dim_customers AS c ON c.customer_id = o.customer_id\n"
    "WHERE o.order_date >= '2024-01-01';\n"
    "TRUNCATE TABLE revenue_daily;\n"
    "INSERT INTO revenue_daily (day, total)\n"
    "SELECT order_date, SUM(amount) FROM revenue_events GROUP BY order_date;\n"
    "GRANT SELECT ON revenue_events TO analyst;\n"
)
# Two raw tables joined, and nothing reads the result.
JOINED_ALONE = (
    "INSERT INTO revenue_events SELECT o.order_id, c.name FROM raw.orders AS o "
    "JOIN raw.customers AS c ON c.id = o.customer_id;\n"
)
# One raw table, and a model that reads the first one.
CHAIN = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders;\n"
    "CREATE TABLE revenue_daily AS SELECT order_id, SUM(amount) AS total "
    "FROM revenue_events GROUP BY order_id;\n"
    "GRANT SELECT ON revenue_daily TO analyst;\n"
)
ONE_APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"


def _layer(change, name: str) -> Decision:  # type: ignore[no-untyped-def]
    (decision,) = [d for d in change.decisions if d.key == f"assemble.layer.{name}"]
    return decision


def _model(change, ending: str):  # type: ignore[no-untyped-def]
    (model,) = [m for m in change.models if m.name.endswith(ending)]
    return model


def _incremental_question(change, table: str) -> Decision:  # type: ignore[no-untyped-def]
    (decision,) = [
        d
        for d in change.decisions
        if d.question and d.key.startswith("tier2.") and table in d.action
    ]
    return decision


# --- 1. a model that joins raw tables is not a staging model


def test_a_model_that_reads_two_raw_tables_is_not_placed_in_staging() -> None:
    change = convert(NIGHTLY, project=THREE)

    events = _model(change, "revenue_events")
    assert events.layer != "staging", "the glossary says a staging model uses no joins"
    question = _layer(change, "revenue_events")
    assert question.question, "the placement is a guess, so it is asked"
    assert [o.kind for o in question.options] == ["intermediate", "mart"]
    # revenue_daily reads it, so the proposal is the middle of the chain.
    assert question.chosen == "put it in intermediate"
    assert events.layer == "intermediate"


def test_a_joined_model_that_nothing_reads_is_proposed_as_a_mart_and_asked() -> None:
    change = convert(JOINED_ALONE, project=THREE)

    question = _layer(change, "revenue_events")
    assert question.chosen == "put it in marts"
    assert _model(change, "revenue_events").layer == "marts"


def test_a_model_that_reads_one_raw_table_stays_in_staging_without_a_question() -> None:
    change = convert(CHAIN, project=THREE)

    assert _model(change, "revenue_events").layer == "staging"
    assert not [d for d in change.decisions if d.key == "assemble.layer.revenue_events"]


def test_every_staging_model_reads_at_most_one_table() -> None:
    """The engine and the glossary agree: a staging model tidies one table."""
    for sql in (NIGHTLY, JOINED_ALONE, CHAIN):
        change = convert(sql, project=THREE)
        for model in change.models:
            if model.layer == "staging":
                assert model.body.count("source(") + model.body.count("ref(") <= 1, model.body


# --- 2. the layer question says the rule that applied to this model


def test_the_layer_plain_reason_names_only_the_rule_that_applied() -> None:
    change = convert(NIGHTLY, project=THREE)

    daily = _layer(change, "revenue_daily").plain_reason
    events = _layer(change, "revenue_events").plain_reason

    for plain in (daily, events):
        assert "or something here reads it" not in plain, plain
        assert "or it reads nothing" not in plain, plain
    assert "Nothing here reads revenue_daily." in daily, daily
    assert "revenue_daily reads other models from this conversion." in daily, daily
    assert "revenue_events combines 2 tables from outside this conversion." in events, events
    assert "Other models here read revenue_events." in events, events


def test_the_layer_reason_names_only_the_rule_that_applied() -> None:
    change = convert(NIGHTLY, project=THREE)

    daily = _layer(change, "revenue_daily").reason
    events = _layer(change, "revenue_events").reason

    assert "A model that other models read is intermediate" not in daily
    assert "no model of this conversion reads revenue_daily" in daily, daily
    assert "revenue_events reads 2 tables that this conversion does not build" in events
    assert "is not a staging model" in events, events


# --- 4. and 5. a layer option describes the materialization the model takes


def test_a_layer_option_for_an_ephemeral_model_promises_no_table() -> None:
    change = convert(CHAIN, project=THREE, materializations={"revenue_daily": "ephemeral"})

    question = _layer(change, "revenue_daily")
    for option in question.options:
        assert "materialized as ephemeral" in option.effect, option.effect
        assert "The table is a promise" not in option.plain, option.plain
        assert "This table is not" not in option.plain, option.plain
        assert "no table or view" in option.plain, option.plain


def test_a_layer_option_for_a_view_says_view() -> None:
    change = convert(CHAIN, project=THREE, materializations={"revenue_daily": "view"})

    for option in _layer(change, "revenue_daily").options:
        assert "view" in option.plain, option.plain
        assert "table" not in option.plain, option.plain


def test_the_intermediate_option_uses_the_detected_default_of_that_layer() -> None:
    """The demo project builds intermediate models as ephemeral. revenue_daily
    takes the mart default where it is proposed, so it takes the
    intermediate default where the answer moves it."""
    pristine = convert(NIGHTLY, project=THREE)
    question = _layer(pristine, "revenue_daily")
    (intermediate,) = [o for o in question.options if o.kind == "intermediate"]
    (mart,) = [o for o in question.options if o.kind == "mart"]
    assert "materialized as ephemeral" in intermediate.effect, intermediate.effect
    assert "materialized as table" in mart.effect, mart.effect

    moved = convert(
        NIGHTLY, project=THREE, answers={question.key: answer_for(question, "intermediate")}
    )
    daily = _model(moved, "revenue_daily")
    assert daily.layer == "intermediate"
    assert daily.materialization is None, "the config is omitted, so the layer default applies"
    omitted = [d for d in moved.decisions if d.key == "assemble.materialization.revenue_daily"]
    assert omitted and "'ephemeral'" in omitted[0].action, omitted


def test_an_incremental_model_keeps_its_own_materialization_in_the_option() -> None:
    change = convert(NIGHTLY, project=THREE)

    for option in _layer(change, "revenue_events").options:
        assert "materialized as incremental" in option.effect, option.effect


# --- 6. the unique key reads as the config writes it


@pytest.mark.parametrize(
    ("columns", "spelled"),
    [
        (("order_id",), "unique_key='order_id'"),
        (("order_id", "amount"), "unique_key=['order_id', 'amount']"),
    ],
)
def test_the_unique_key_in_the_decision_matches_the_config(
    columns: tuple[str, ...], spelled: str
) -> None:
    question = _incremental_question(convert(ONE_APPEND), "revenue_events")
    change = convert(ONE_APPEND, answers={question.key: answer_for(question, "merge", columns)})

    (model,) = change.models
    assert spelled in render_model(model)
    rewritten = next(d for d in change.decisions if d.key == question.key)
    assert spelled in rewritten.action, rewritten.action


def test_the_unique_key_under_a_build_choice_matches_the_config_spelling() -> None:
    question = _incremental_question(convert(ONE_APPEND), "revenue_events")
    change = convert(
        ONE_APPEND,
        answers={question.key: answer_for(question, "merge", ("order_id",))},
        materializations={"stg_revenue_events": "table"},
    )

    rewritten = next(d for d in change.decisions if d.key == question.key)
    assert "unique_key='order_id'" in rewritten.action, rewritten.action


# --- 7. and 8. the rewrite record counts and tells the truth


def test_one_reference_is_singular() -> None:
    change = convert(CHAIN, project=THREE)

    (rewrite,) = [d for d in change.decisions if d.key == "assemble.rewrite.revenue_daily"]
    assert "1 reference rewritten to ref()" in rewrite.action, rewrite.action
    assert "reference(s)" not in rewrite.action


def test_two_references_are_plural() -> None:
    change = convert(JOINED_ALONE, project=THREE)

    (rewrite,) = [d for d in change.decisions if d.key.startswith("assemble.rewrite.")]
    assert "2 references rewritten to source()" in rewrite.action, rewrite.action


def test_a_model_with_no_reference_is_not_said_to_run_as_jinja() -> None:
    change = convert("CREATE TABLE constants AS SELECT 1 AS one;\n", project=THREE)

    (rewrite,) = [d for d in change.decisions if d.key.startswith("assemble.rewrite.")]
    assert "reads no table" in rewrite.action, rewrite.action
    assert "Jinja" not in rewrite.reason, rewrite.reason
    assert rewrite.chosen


def test_a_model_whose_references_all_stay_as_written_is_not_said_to_be_rewritten() -> None:
    change = convert("CREATE TABLE copy_of AS SELECT id FROM legacy_table;\n", project=THREE)

    (rewrite,) = [d for d in change.decisions if d.key.startswith("assemble.rewrite.")]
    assert not rewrite.action.startswith("rewrote"), rewrite.action
    assert "no references needed rewriting" not in rewrite.action
    assert "runs as dbt Jinja" not in rewrite.reason, rewrite.reason
    assert [d for d in change.decisions if d.key.startswith("assemble.rewrite_unresolved.")]


def test_a_model_with_some_references_left_says_how_many() -> None:
    sql = (
        "CREATE TABLE mixed AS SELECT o.id FROM raw.orders AS o "
        "JOIN legacy_table AS l ON l.id = o.id;\n"
    )
    change = convert(sql, project=THREE)

    (rewrite,) = [d for d in change.decisions if d.key.startswith("assemble.rewrite.")]
    assert "1 reference rewritten to source()" in rewrite.action, rewrite.action
    assert "1 reference left as written" in rewrite.action, rewrite.action
    assert "1 of 2" in rewrite.reason, rewrite.reason


def test_the_unqualified_table_record_does_not_promise_a_later_resolution() -> None:
    change = convert("CREATE TABLE copy_of AS SELECT id FROM legacy_table;\n", project=THREE)

    (record,) = [d for d in change.decisions if d.key == "assemble.source_unqualified.legacy_table"]
    assert "Tier 2" not in record.reason, record.reason
    assert "until" not in record.reason, record.reason


# --- 9. the cross-project question


def test_the_cross_project_options_are_full_true_sentences() -> None:
    elsewhere = (context_for("core_platform"),)
    change = convert(NIGHTLY, project=THREE, elsewhere=elsewhere)

    (question,) = [d for d in change.decisions if d.key == "assemble.cross_ref.dim_customers"]
    for option in question.options:
        for text in (option.effect, option.plain):
            assert text[0].isupper() or text.startswith("dbt"), text
            assert text.endswith("."), text
    (source,) = [o for o in question.options if o.kind == "source"]
    assert "declares dim_customers in its sources.yml" not in source.effect, source.effect

    taken = convert(
        NIGHTLY,
        project=THREE,
        elsewhere=elsewhere,
        answers={question.key: answer_for(question, "cross_ref")},
    )
    (answered,) = [d for d in taken.decisions if d.key == "assemble.cross_ref.dim_customers"]
    assert answered.action.count("dim_customers") == 1, answered.action


# --- 10. grants after "Built as: ephemeral"


def test_the_grants_record_says_an_ephemeral_model_applies_none() -> None:
    change = convert(CHAIN, project=THREE, materializations={"revenue_daily": "ephemeral"})

    (grants,) = [d for d in change.decisions if d.key.startswith("tier1.grants.")]
    assert "dbt applies the grants after each build" not in grants.reason, grants.reason
    assert "ephemeral" in grants.reason, grants.reason
    assert "ephemeral" in grants.action, grants.action


def test_the_grants_record_is_unchanged_for_a_table() -> None:
    change = convert(CHAIN, project=THREE)

    (grants,) = [d for d in change.decisions if d.key.startswith("tier1.grants.")]
    assert "dbt applies the grants after each build" in grants.reason


# --- 11. refusals a reader can cause carry no internal key


def test_the_unknown_column_refusal_names_the_option_and_the_table_not_the_key() -> None:
    question = _incremental_question(convert(ONE_APPEND), "revenue_events")
    with pytest.raises(UnknownAnswerError) as refused:
        convert(ONE_APPEND, answers={question.key: Answer(merge_option().label, ("nope",))})

    message = str(refused.value)
    assert question.key not in message
    assert not message.startswith("tier2.")
    assert repr(merge_option().label) in message
    assert "revenue_events" in message
    assert "order_id, amount" in message, "what is allowed"
    assert "press it again" in message, "what to do"


def test_the_two_column_checked_refusal_names_the_option_and_the_table_not_the_key() -> None:
    question = _incremental_question(convert(ONE_APPEND), "revenue_events")
    with pytest.raises(UnknownAnswerError) as refused:
        convert(
            ONE_APPEND,
            answers={question.key: Answer(verify_option().label, ("order_id", "amount"))},
        )

    message = str(refused.value)
    assert question.key not in message
    assert repr(verify_option().label) in message
    assert "revenue_events" in message
    assert "can name only one column" in message
    assert "press it again" in message


# --- 12. layer options read as sentences on their own


def test_layer_option_texts_are_sentences() -> None:
    change = convert(NIGHTLY, project=THREE)

    for name in ("revenue_events", "revenue_daily"):
        for option in _layer(change, name).options:
            for text in (option.effect, option.plain):
                assert text[0].isupper(), text
                assert text.endswith("."), text
