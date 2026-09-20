"""render_schema_yaml: one model's schema .yml -- its tests, its description.

The model is named rather than derived from the entries. It used to be
derived, on the grounds that a separate name parameter could disagree with
what the tests name -- still true, and still refused below. What retired that
argument is that a .yml now has content carrying no model name of its own: a
description with no test. The name has to come from the caller because there
are files this renders where nothing else knows it.
"""

import pytest

from dbtw.core.emit.render import render_schema_yaml
from dbtw.core.passes import SchemaTest


def test_the_yaml_declares_exactly_the_chosen_test():
    out = render_schema_yaml("stg_events", (SchemaTest("stg_events", "order_id"),))
    assert "name: stg_events" in out
    assert "name: order_id" in out
    assert "- unique" in out
    assert "not_null" not in out  # the invented-decision pin


def test_nothing_to_say_renders_nothing():
    """Neither a test nor a description. A .yml holding a name and nothing
    else says nothing, and writing one would put an empty file beside every
    model in someone's project."""
    assert render_schema_yaml("stg_events") == ""
    assert render_schema_yaml("stg_events", (), "   ") == ""


def test_a_description_alone_renders_a_schema():
    out = render_schema_yaml("stg_events", (), "Events, one row per click.")
    assert "name: stg_events" in out
    assert "description: Events, one row per click." in out
    assert "columns:" not in out, "a model with no test should declare none"


def test_a_description_and_tests_render_under_one_model_entry():
    out = render_schema_yaml("stg_events", (SchemaTest("stg_events", "order_id"),), "Events.")
    assert out.count("name: stg_events") == 1
    assert "description: Events." in out
    assert "- unique" in out


def test_two_tests_on_the_same_model_render_two_column_entries():
    out = render_schema_yaml(
        "stg_events",
        (
            SchemaTest("stg_events", "order_id"),
            SchemaTest("stg_events", "line_id"),
        ),
    )
    assert out.count("name: stg_events") == 1  # one models: entry, not two
    assert "name: order_id" in out
    assert "name: line_id" in out
    assert out.count("- unique") == 2


def test_tests_naming_a_model_other_than_the_one_being_rendered_are_refused():
    """A caller bug, not a user input: writer.emit() groups change.tests by
    model before calling this, so a mixed call never happens through the
    pipeline. Refusing loudly here means that grouping bug surfaces as an
    error instead of silently writing one model's test under another's
    models: entry.
    """
    with pytest.raises(ValueError, match="stg_customers"):
        render_schema_yaml(
            "stg_events",
            (
                SchemaTest("stg_events", "order_id"),
                SchemaTest("stg_customers", "customer_id"),
            ),
        )


def test_one_test_naming_a_model_other_than_the_one_being_rendered_is_refused():
    """One test, naming the wrong model -- which is the case the model became
    a parameter for.

    While the model was derived from the tests, a single test could not
    disagree with anything: it *was* the name. Now the caller supplies the
    name, so one test naming another model is a file written under the wrong
    `name:` with no second test to make the set look mixed. Checked with one,
    because the two-test version above passes under a check counting distinct
    names -- which is what the check used to be, and what a mutation reverting
    it to that still passes.
    """
    with pytest.raises(ValueError, match="stg_customers"):
        render_schema_yaml("stg_events", (SchemaTest("stg_customers", "customer_id"),))
