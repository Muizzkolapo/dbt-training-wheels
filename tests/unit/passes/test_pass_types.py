import dataclasses

import pytest

from dbtw.core.passes import Decision, ModelDraft, PassState


def test_decision_is_immutable_and_carries_location():
    d = Decision(
        key="tier1.build.t.sql:0",
        tier=1,
        action="created model dim_customers (materialized='table')",
        reason="dbt models are SELECT statements; CREATE becomes configuration",
        source_file="t.sql",
        line_start=1,
        line_end=2,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.tier = 2  # type: ignore[misc]
    assert d.reason


def test_model_draft_defaults():
    draft = ModelDraft(
        name="dim_customers",
        qualified_name="analytics.dim_customers",
        identity=("", "analytics", "dim_customers"),
        body="SELECT 1 AS a",
        materialization="table",
        grants=(),
        source_indices=(4,),
        leading_comments=(),
    )
    assert draft.grants == ()
    assert draft.source_indices == (4,)
    assert draft.qualified_name == "analytics.dim_customers"


def test_pass_state_holds_pending_with_indices():
    state = PassState(pending=(), drafts=(), decisions=(), dialect="tsql")
    assert state.pending == ()
    assert state.dialect == "tsql"
