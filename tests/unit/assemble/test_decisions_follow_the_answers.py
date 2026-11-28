"""Every Decision describes the model this run writes, after the reader's answers.

`assemble` re-runs on every answer. Browser testers found Decisions that still
described the conversion from before the answer: a layer question that said
"placed revenue_daily in the mart layer" after the reader chose intermediate,
an incremental question that said "became an incremental model" beside a
model the reader built as a view, and merge caveats beside a model that no
longer merges. Each test below gives an answer and then reads the Decisions
against the model that came out.
"""

from __future__ import annotations

import pytest
from tests.unit.assemble.helpers import convert

from dbtw.core.assemble import UnknownAnswerError
from dbtw.core.passes import Answer, Decision, answer_for, statement_index

THREE = "three_layers"

# A staging model, and a mart that reads it. The mart is a CTAS, so it is a
# `table` in a layer that defaults to `table`, and its placement is the one
# question the engine asks about layers. The GRANT folds into its config.
CHAIN = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders;\n"
    "CREATE TABLE revenue_daily AS SELECT order_id, SUM(amount) AS total "
    "FROM revenue_events GROUP BY order_id;\n"
    "GRANT SELECT ON revenue_daily TO analyst;\n"
)
ONE_APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
FILTERED_APPEND = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders WHERE amount > 0;\n"
)
# `amount * 2` has no output name, so the columns of this model are not all
# knowable from the SQL text.
UNNAMED_APPEND = "INSERT INTO revenue_events SELECT order_id, amount * 2 FROM stg_orders;\n"
# No WHEN NOT MATCHED branch, so tier 2 records a merge caveat for it.
UPDATE_ONLY_MERGE = (
    "MERGE INTO dim_c AS t USING raw.customers AS s ON t.customer_id = s.customer_id "
    "WHEN MATCHED THEN UPDATE SET t.name = s.name;\n"
)


def _by_prefix(decisions: tuple[Decision, ...], prefix: str) -> list[Decision]:
    return [d for d in decisions if d.key.startswith(prefix)]


def _question(change, table: str) -> Decision:  # type: ignore[no-untyped-def]
    (decision,) = [
        d
        for d in change.decisions
        if d.question and d.key.startswith("tier2.") and table in d.action
    ]
    return decision


# --- 1. the layer question


def test_the_layer_decision_names_the_layer_the_answer_chose() -> None:
    pristine = convert(CHAIN, project=THREE)
    (question,) = _by_prefix(pristine.decisions, "assemble.layer.")
    assert question.action == "placed revenue_daily in the mart layer"

    change = convert(
        CHAIN, project=THREE, answers={question.key: answer_for(question, "intermediate")}
    )

    (answered,) = _by_prefix(change.decisions, "assemble.layer.")
    (model,) = [m for m in change.models if m.name.endswith("revenue_daily")]
    assert model.layer == "intermediate"
    assert "intermediate layer" in answered.action
    assert "mart" not in answered.action
    assert "answer" in answered.reason


def test_an_unanswered_layer_decision_does_not_claim_an_answer() -> None:
    change = convert(CHAIN, project=THREE)

    (question,) = _by_prefix(change.decisions, "assemble.layer.")

    assert "answer" not in question.reason


def test_the_layer_options_say_the_materialization_the_reader_chose() -> None:
    change = convert(CHAIN, project=THREE, materializations={"revenue_daily": "view"})

    (question,) = _by_prefix(change.decisions, "assemble.layer.")
    effects = [option.effect for option in question.options]
    assert effects, "the question still offers its layers"
    assert all("materialized as view" in effect for effect in effects), effects
    assert not any("as table" in effect for effect in effects), effects


def test_a_materialization_choice_removes_the_layer_default_decision() -> None:
    """The model matches its layer default only until the reader picks another
    value. Then the config names the value, and "materialized config omitted"
    describes a file this run does not write."""
    pristine = convert(CHAIN, project=THREE)
    assert _by_prefix(pristine.decisions, "assemble.materialization.revenue_daily")

    change = convert(CHAIN, project=THREE, materializations={"revenue_daily": "view"})

    (model,) = [m for m in change.models if m.name == "revenue_daily"]
    assert model.materialization == "view"
    assert not _by_prefix(change.decisions, "assemble.materialization.revenue_daily")


# --- 2. the incremental question under a "Built as" choice


@pytest.mark.parametrize("chosen", ["ephemeral", "table", "view"])
def test_the_incremental_question_says_the_build_choice_replaced_it(chosen: str) -> None:
    question = _question(convert(ONE_APPEND), "revenue_events")
    answer = answer_for(question, "merge", ("order_id",))

    change = convert(
        ONE_APPEND,
        answers={question.key: answer},
        materializations={"stg_revenue_events": chosen},
    )

    (model,) = change.models
    assert (model.materialization, model.incremental_strategy) == (chosen, None)
    rewritten = next(d for d in change.decisions if d.key == question.key)
    assert "became an incremental model" not in rewritten.action
    assert f"built as {chosen}" in rewritten.action
    assert "unique_key='order_id'" in rewritten.action, "the replaced config is named"
    assert chosen in rewritten.reason
    # The question stays a question: the answer is still the reader's, and it
    # applies again when the build choice is removed.
    assert rewritten.question == question.question
    assert rewritten.chosen == "merge on order_id"


def test_without_a_build_choice_the_incremental_question_is_unchanged() -> None:
    question = _question(convert(ONE_APPEND), "revenue_events")

    change = convert(ONE_APPEND, answers={question.key: answer_for(question, "append")})

    rewritten = next(d for d in change.decisions if d.key == question.key)
    assert rewritten == question


def test_a_merge_question_under_a_build_choice_says_so_too() -> None:
    question = _question(convert(UPDATE_ONLY_MERGE), "dim_c")

    change = convert(UPDATE_ONLY_MERGE, materializations={"stg_dim_c": "table"})

    rewritten = next(d for d in change.decisions if d.key == question.key)
    assert "became an incremental model" not in rewritten.action
    assert "built as table" in rewritten.action


# --- 3. caveats that are no longer true


def test_merge_caveats_go_when_the_answer_switches_the_merge_to_append() -> None:
    pristine = convert(UPDATE_ONLY_MERGE)
    assert _by_prefix(pristine.decisions, "tier2.merge.no_insert_branch.")
    question = _question(pristine, "dim_c")

    change = convert(UPDATE_ONLY_MERGE, answers={question.key: answer_for(question, "append")})

    (model,) = change.models
    assert model.incremental_strategy == "append"
    assert not _by_prefix(change.decisions, "tier2.merge.no_insert_branch.")


def test_merge_caveats_stay_while_the_model_still_merges() -> None:
    question = _question(convert(UPDATE_ONLY_MERGE), "dim_c")

    change = convert(
        UPDATE_ONLY_MERGE, answers={question.key: answer_for(question, "merge_checked")}
    )

    assert _by_prefix(change.decisions, "tier2.merge.no_insert_branch.")


def test_merge_caveats_go_when_the_model_is_built_as_a_view() -> None:
    change = convert(UPDATE_ONLY_MERGE, materializations={"stg_dim_c": "view"})

    assert not _by_prefix(change.decisions, "tier2.merge.no_insert_branch.")


def test_the_incremental_filter_caveat_goes_when_the_model_is_built_as_a_table() -> None:
    pristine = convert(FILTERED_APPEND)
    assert _by_prefix(pristine.decisions, "assemble.incremental_filter.")

    change = convert(FILTERED_APPEND, materializations={"stg_revenue_events": "table"})

    assert not _by_prefix(change.decisions, "assemble.incremental_filter.")


# --- 4. a typed key the model does not select


def test_a_typed_key_the_model_does_not_select_is_refused_with_the_columns_it_selects() -> None:
    question = _question(convert(ONE_APPEND), "revenue_events")

    with pytest.raises(UnknownAnswerError) as refused:
        convert(
            ONE_APPEND, answers={question.key: Answer("merge on a unique key", ("no_such_col",))}
        )

    message = str(refused.value)
    assert "no_such_col" in message
    assert "order_id, amount" in message


def test_the_checked_option_refuses_an_unselected_key_too() -> None:
    question = _question(convert(ONE_APPEND), "revenue_events")
    checked = answer_for(question, "merge_checked", ("no_such_col",))

    with pytest.raises(UnknownAnswerError, match="no_such_col"):
        convert(ONE_APPEND, answers={question.key: checked})


def test_a_typed_key_the_model_selects_is_still_applied() -> None:
    question = _question(convert(ONE_APPEND), "revenue_events")

    change = convert(
        ONE_APPEND, answers={question.key: Answer("merge on a unique key", ("order_id",))}
    )

    (model,) = change.models
    assert (model.incremental_strategy, model.unique_key) == ("merge", ("order_id",))


def test_where_the_columns_are_not_knowable_the_key_is_declined_with_a_reason() -> None:
    question = _question(convert(UNNAMED_APPEND), "revenue_events")
    assert question.subject is not None and question.subject.candidates == ()

    change = convert(
        UNNAMED_APPEND, answers={question.key: Answer("merge on a unique key", ("no_such_col",))}
    )

    (model,) = change.models
    assert (model.incremental_strategy, model.unique_key) == ("append", ())
    assert _by_prefix(change.decisions, "assemble.unique_key_not_selected.revenue_events")


# --- 5. an ephemeral model


def test_grants_on_an_ephemeral_model_get_a_caveat() -> None:
    change = convert(CHAIN, project=THREE, materializations={"revenue_daily": "ephemeral"})

    (model,) = [m for m in change.models if m.name == "revenue_daily"]
    assert model.grants == (("SELECT", ("analyst",)),)
    (caveat,) = _by_prefix(change.decisions, "assemble.ephemeral_grants.revenue_daily")
    assert "analyst" in caveat.reason
    assert caveat.plain_reason


def test_grants_on_a_view_get_no_ephemeral_caveat() -> None:
    change = convert(CHAIN, project=THREE, materializations={"revenue_daily": "view"})

    assert not _by_prefix(change.decisions, "assemble.ephemeral_grants.")


def test_an_ephemeral_model_that_nothing_reads_gets_a_caveat() -> None:
    change = convert(CHAIN, project=THREE, materializations={"revenue_daily": "ephemeral"})

    (caveat,) = _by_prefix(change.decisions, "assemble.ephemeral_unread.revenue_daily")
    assert "revenue_daily" in caveat.action
    assert caveat.plain_reason


def test_an_ephemeral_model_that_another_model_reads_gets_no_unread_caveat() -> None:
    change = convert(CHAIN, project=THREE, materializations={"revenue_events": "ephemeral"})

    (model,) = [m for m in change.models if m.name == "revenue_events"]
    assert model.materialization == "ephemeral"
    assert not _by_prefix(change.decisions, "assemble.ephemeral_unread.")
    assert not _by_prefix(change.decisions, "assemble.ephemeral_grants.")


# MERGE statements that between them reach every caveat `_merge_caveats`
# records: no insert branch, no update branch, restricted SET and INSERT
# lists with and without a projection, a value from elsewhere, and a branch
# condition.
CAVEAT_MERGES = (
    UPDATE_ONLY_MERGE,
    "MERGE INTO dim_i AS t USING raw.items AS s ON t.id = s.id "
    "WHEN NOT MATCHED THEN INSERT (id, name) VALUES (s.id, s.name);\n",
    "MERGE INTO dim_r AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED AND s.hot THEN UPDATE SET t.city = s.city, t.email = s.email "
    "WHEN MATCHED THEN UPDATE SET t.CITY = s.city "
    "WHEN NOT MATCHED THEN INSERT (id, city, email) VALUES (s.id, s.city, s.email);\n",
    "MERGE INTO dim_w AS t USING (SELECT * FROM stg_c) AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.city = UPPER(s.city) "
    "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id);\n",
)


def test_every_merge_caveat_tier2_records_is_one_that_assemble_can_remove() -> None:
    """`assemble` finds the merge caveats by `tier2.MERGE_CAVEATS`. A caveat
    name missing from that list stays beside a model that no longer merges."""
    from dbtw.core.passes.tier2 import MERGE_CAVEATS

    seen: set[str] = set()
    for sql in CAVEAT_MERGES:
        for decision in convert(sql).decisions:
            if decision.key.startswith("tier2.merge.") and decision.action.startswith("caveat:"):
                name = "merge." + decision.key.split(".")[2]
                assert name in MERGE_CAVEATS, name
                seen.add(name)
    assert seen == set(MERGE_CAVEATS), set(MERGE_CAVEATS) - seen


def test_a_layer_answer_and_a_build_choice_on_one_model_both_reach_the_decision() -> None:
    """The answer moves the model, and the build choice changes how it is
    built. The one layer Decision describes both."""
    pristine = convert(CHAIN, project=THREE)
    (question,) = _by_prefix(pristine.decisions, "assemble.layer.")

    change = convert(
        CHAIN,
        project=THREE,
        answers={question.key: answer_for(question, "intermediate")},
        materializations={"revenue_daily": "view"},
    )

    (answered,) = _by_prefix(change.decisions, "assemble.layer.")
    assert "intermediate layer" in answered.action
    assert all("materialized as view" in option.effect for option in answered.options)
    (model,) = [m for m in change.models if m.name == "revenue_daily"]
    assert model.materialization == "view"


# --- the Decisions that record a model's materialization when it is drafted

# Each script drafts one model, and one tier-1 or tier-2 Decision records the
# materialization of that draft in its action: (script, the Decision name, the
# drafted materialization, a "Built as" choice that differs from it).
DRAFTED = {
    "build": ("CREATE TABLE dim_people AS SELECT id, name FROM raw.people;\n", "table", "view"),
    "select": ("SELECT id, name FROM raw.people;\n", "view", "table"),
    "truncate_insert": (
        "TRUNCATE TABLE daily;\nINSERT INTO daily SELECT id, SUM(x) AS t FROM raw.e GROUP BY id;\n",
        "table",
        "ephemeral",
    ),
    "truncate_insert_columns": (
        "TRUNCATE TABLE daily;\n"
        "INSERT INTO daily (day, total) SELECT id, SUM(x) FROM raw.e GROUP BY id;\n",
        "table",
        "view",
    ),
}


def _drafted_record(change, name: str) -> Decision:  # type: ignore[no-untyped-def]
    (record,) = [
        d
        for d in change.decisions
        if d.key == f"{d.key.split('.')[0]}.{name}.{d.source_file}:{statement_index(d)}"
    ]
    return record


@pytest.mark.parametrize("name", sorted(DRAFTED))
def test_a_build_choice_reaches_the_decision_that_recorded_the_draft(name: str) -> None:
    sql, drafted, chosen = DRAFTED[name]
    pristine = convert(sql)
    (model,) = pristine.models
    before = _drafted_record(pristine, name)
    assert f"materialized='{drafted}'" in before.action

    change = convert(sql, materializations={model.name: chosen})

    (built,) = change.models
    assert built.materialization == chosen
    after = _drafted_record(change, name)
    assert f"materialized='{chosen}'" in after.action
    assert "choice" in after.action
    assert chosen in after.reason
    assert chosen in after.plain_reason


@pytest.mark.parametrize("name", sorted(DRAFTED))
def test_a_build_choice_equal_to_the_draft_leaves_the_decision_alone(name: str) -> None:
    sql, drafted, _ = DRAFTED[name]
    pristine = convert(sql)
    (model,) = pristine.models

    change = convert(sql, materializations={model.name: drafted})

    assert _drafted_record(change, name) == _drafted_record(pristine, name)


def test_the_refusal_names_each_selected_column_once() -> None:
    sql = "INSERT INTO revenue_events SELECT order_id, amount AS order_id, note FROM stg_orders;\n"
    question = _question(convert(sql), "revenue_events")

    with pytest.raises(UnknownAnswerError) as refused:
        convert(sql, answers={question.key: Answer("merge on a unique key", ("no_such_col",))})

    assert "are order_id, note." in str(refused.value)


def test_every_pass_decision_that_names_a_materialization_is_a_record_assemble_rewrites() -> None:
    """`assemble` finds these Decisions by the `DRAFT_MATERIALIZATION_RECORDS`
    lists. A Decision that names a materialization and is missing from them
    keeps the drafted value beside a model that the reader built otherwise."""
    from dbtw.core.assemble.assembler import _records_drafted_materialization

    named = [
        d
        for sql, _, _ in DRAFTED.values()
        for d in convert(sql).decisions
        if d.key.startswith("tier") and "materialized='" in d.action
    ]
    assert len(named) == len(DRAFTED)
    assert all(_records_drafted_materialization(d) for d in named), [d.key for d in named]
