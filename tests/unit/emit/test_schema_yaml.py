"""render_schema_yaml: the .yml content for the tests an answer asked for.

One argument only -- the tests tuple -- not a separate model_name alongside
it. A second parameter naming the model would let a caller pass entries for
one model under another's name; deriving the name from the entries themselves
makes that pair impossible to disagree, at the cost of a caller that mixes
two models' tests in one call getting a loud refusal instead of a wrong file.
"""

import pytest

from dbtw.core.emit.render import render_schema_yaml
from dbtw.core.passes import SchemaTest


def test_the_yaml_declares_exactly_the_chosen_test():
    out = render_schema_yaml((SchemaTest("stg_events", "order_id"),))
    assert "name: stg_events" in out
    assert "name: order_id" in out
    assert "- unique" in out
    assert "not_null" not in out  # the invented-decision pin


def test_no_tests_renders_nothing():
    assert render_schema_yaml(()) == ""


def test_two_tests_on_the_same_model_render_two_column_entries():
    out = render_schema_yaml(
        (
            SchemaTest("stg_events", "order_id"),
            SchemaTest("stg_events", "line_id"),
        )
    )
    assert out.count("name: stg_events") == 1  # one models: entry, not two
    assert "name: order_id" in out
    assert "name: line_id" in out
    assert out.count("- unique") == 2


def test_tests_naming_more_than_one_model_in_one_call_are_refused():
    """A caller bug, not a user input: writer.emit() groups change.tests by
    model before calling this, so a mixed call never happens through the
    pipeline. Refusing loudly here means that grouping bug surfaces as an
    error instead of silently writing one model's test under another's
    models: entry.
    """
    with pytest.raises(ValueError, match="stg_customers"):
        render_schema_yaml(
            (
                SchemaTest("stg_events", "order_id"),
                SchemaTest("stg_customers", "customer_id"),
            )
        )
