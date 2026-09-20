"""Labels so models can be run in groups later.

The design's step 7: "tags are how you say 'run only the hourly finance
models' six months from now. Two or three tags per model is plenty; a tag on
everything is a tag on nothing."

Nothing in a script says which models somebody means to run together, so this
engine carries tags and never invents them -- the same rule a description
lives under, for the same reason.
"""

from __future__ import annotations

import pytest
from tests.unit.assemble.helpers import convert

from dbtw.core.assemble import UnknownModelError
from dbtw.core.emit.render import render_model

ONE = "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders;\n"
MODEL = "stg_revenue_events"


def test_tags_reach_the_models_config_block() -> None:
    change = convert(ONE, tags={MODEL: ["finance", "daily"]})

    (model,) = change.models

    assert model.tags == ("finance", "daily")
    assert "tags=['finance', 'daily']" in render_model(model)


def test_a_model_nobody_tagged_carries_none() -> None:
    """A tag on everything is a tag on nothing, and an empty `tags=[]` in
    somebody's config block claims a grouping they did not make.
    """
    change = convert(ONE)

    (model,) = change.models

    assert model.tags == ()
    assert "tags" not in render_model(model)


def test_the_order_a_reader_typed_is_the_order_that_is_written() -> None:
    change = convert(ONE, tags={MODEL: ["zulu", "alpha"]})

    assert change.models[0].tags == ("zulu", "alpha")


def test_blanks_are_dropped_and_a_repeat_is_kept_once() -> None:
    """`tags=['finance', 'finance']` is a list dbt reads twice and a reader
    wrote once."""
    change = convert(ONE, tags={MODEL: [" finance ", "", "daily", "finance", "   "]})

    assert change.models[0].tags == ("finance", "daily")


def test_a_tag_naming_a_model_this_conversion_does_not_build_is_refused() -> None:
    """Refused rather than dropped, for the reason a description naming one
    is: the walk would have shown the reader their own words on a screen,
    computed them into nothing, and said so nowhere.
    """
    with pytest.raises(UnknownModelError) as refused:
        convert(ONE, tags={"not_a_model_here": ["finance"]})

    assert "not_a_model_here" in str(refused.value)


def test_tags_sit_after_the_incremental_config_and_before_grants() -> None:
    """A config block is read top to bottom by somebody checking it against
    what they asked for, so the order is dbt's own docs' order.
    """
    change = convert(
        "INSERT INTO revenue_events SELECT order_id FROM raw_orders;\n"
        "GRANT SELECT ON revenue_events TO analyst;\n",
        tags={MODEL: ["finance"]},
    )

    rendered = render_model(change.models[0])

    assert rendered.index("incremental_strategy") < rendered.index("tags=")
    assert rendered.index("tags=") < rendered.index("grants=")
