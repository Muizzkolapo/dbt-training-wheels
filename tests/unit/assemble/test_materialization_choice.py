"""What a model is built as, when the reader wants something else.

The design's step 6: "view is cheap and always fresh; table is expensive and
fast to query. The defaults here are conventional and safe. Change them when
a model gets slow, not before."

So this is an override, not a question: the conversion's own answer stands
until somebody says otherwise. What makes it worth its own code is that it is
not additive. A model this conversion made incremental carries a strategy and
possibly a key, and neither means anything once it is a view or a table.
"""

from __future__ import annotations

import pytest
from tests.unit.assemble.helpers import convert

from dbtw.core.assemble import UnknownModelError, UnsupportedMaterializationError
from dbtw.core.emit.render import render_model

INSERT = "INSERT INTO revenue_events SELECT order_id FROM raw_orders;\n"
MODEL = "stg_revenue_events"


def _dropped(change):  # type: ignore[no-untyped-def]
    return [d for d in change.decisions if d.key.startswith("assemble.materialization_dropped.")]


def test_the_conversions_own_answer_stands_until_somebody_changes_it() -> None:
    change = convert(INSERT)

    (model,) = change.models

    assert model.materialization == "incremental"
    assert model.incremental_strategy == "append"
    assert not _dropped(change)


def test_choosing_a_table_drops_the_incremental_config_and_says_so() -> None:
    """dbt reads incremental_strategy only for an incremental model. Writing
    it beside `materialized='table'` would put two lines in somebody's config
    that dbt ignores; dropping them without a word would take away two lines
    they answered a question to put there.
    """
    change = convert(INSERT, materializations={MODEL: "table"})

    (model,) = change.models

    assert model.materialization == "table"
    assert model.incremental_strategy is None
    assert model.unique_key == ()
    rendered = render_model(model)
    assert "incremental_strategy" not in rendered
    (said,) = _dropped(change)
    assert MODEL in said.action
    assert "table" in said.action


def test_a_model_that_was_never_incremental_loses_nothing_and_says_nothing() -> None:
    """The Decision is about what the change cost. A model with no
    incremental config to drop cost nothing, and a caveat about a loss that
    did not happen is one more thing the report says that is not there.
    """
    change = convert(
        "SELECT id, name INTO dim_people FROM raw_people;\n",
        materializations={"stg_dim_people": "view"},
    )

    (model,) = change.models
    assert model.materialization == "view"
    assert not _dropped(change)


def test_a_materialization_this_walk_does_not_offer_is_refused() -> None:
    """`incremental` is the one that matters here. dbt accepts it and runs it
    with whatever strategy the adapter defaults to, so it is the failure that
    looks like it worked -- and whether a model is incremental is settled by
    the question this walk already asks, which carries a strategy and a key.
    """
    with pytest.raises(UnsupportedMaterializationError) as refused:
        convert(INSERT, materializations={MODEL: "incremental"})

    assert "incremental" in str(refused.value)

    with pytest.raises(UnsupportedMaterializationError):
        convert(INSERT, materializations={MODEL: "tabel"})


def test_a_materialization_naming_a_model_this_conversion_does_not_build_is_refused() -> None:
    with pytest.raises(UnknownModelError):
        convert(INSERT, materializations={"not_a_model_here": "table"})


def test_choosing_what_the_conversion_already_chose_changes_nothing() -> None:
    """A reader who opened the select and picked the value already there has
    not made a change, and a caveat saying they did would be this tool
    inventing an edit.

    The model here converts to `table` on its own -- jaffle_shop's mart layer
    says so -- so asking for `table` is asking for what is already true.
    """
    untouched = convert("SELECT id, name INTO dim_people FROM raw_people;\n")
    (before,) = untouched.models
    assert before.materialization == "table", "this fixture should give a table already"

    change = convert(
        "SELECT id, name INTO dim_people FROM raw_people;\n",
        materializations={before.name: "table"},
    )

    (after,) = change.models
    assert after == before
    assert not _dropped(change)
