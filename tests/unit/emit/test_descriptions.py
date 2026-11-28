"""A model's description: the one field the walk insists on, and where it lands.

The persona study's third pillar is "Asks -- you describe what each model is
for. The only field it insists on." A description is not a choice among
options the engine offers, so it is not an Answer: it is text the reader
writes, carried through the pipeline and rendered into the model's own
schema .yml, where dbt reads it and `dbt docs` shows it.

What matters here is that a description is enough on its own to make a .yml
exist. Before this, a .yml was written only for a model an answer had asked a
test for, so a described model with no test would have had its description
computed and then dropped on the floor.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from tests.unit.assemble.helpers import context_for, convert

from dbtw.core.assemble import JinjaInDescriptionError
from dbtw.core.emit import emit

ONE_MODEL = "INSERT INTO revenue_events SELECT order_id, amount FROM raw.orders;\n"


def _described(text: str, sql: str = ONE_MODEL):  # type: ignore[no-untyped-def]
    change = convert(sql)
    (model,) = [m for m in change.models if "revenue_events" in m.name]
    return convert(sql, descriptions={model.name: text}), model.name


def test_a_description_reaches_the_models_schema_yaml(tmp_path: Path) -> None:
    change, name = _described("Revenue events, one row per order line.")
    emit(change, context_for("jaffle_shop"), tmp_path)

    doc = yaml.safe_load((tmp_path / "models" / "staging" / f"{name}.yml").read_text())
    (model_doc,) = doc["models"]
    assert model_doc["name"] == name
    assert model_doc["description"] == "Revenue events, one row per order line."


def test_a_description_alone_is_enough_to_write_a_schema_yaml(tmp_path: Path) -> None:
    """No answer, so no test -- and the .yml has to exist anyway. A model with
    a description and no test is the ordinary case: describing every model is
    asked of every reader, and answering a question is not.
    """
    change, name = _described("What this is for.")
    assert change.tests == (), "this conversion was not answered, so it has no tests"

    emit(change, context_for("jaffle_shop"), tmp_path)

    written = (tmp_path / "models" / "staging" / f"{name}.yml").read_text()
    assert "description: What this is for." in written
    assert "tests:" not in written, "a model with no test should declare none"


def test_a_model_with_neither_gets_no_schema_yaml(tmp_path: Path) -> None:
    """The control. A .yml holding a name and nothing else says nothing, and
    writing one would put an empty file in someone's project for every model.
    """
    change = convert(ONE_MODEL)
    emit(change, context_for("jaffle_shop"), tmp_path)

    assert not list(tmp_path.rglob("*.yml")) or not [
        p for p in tmp_path.rglob("*.yml") if p.name != "sources.yml"
    ]


def test_a_description_and_a_test_share_one_schema_yaml(tmp_path: Path) -> None:
    """One file per model, not one per thing said about it."""
    from dbtw.core.passes import Answer, verify_option

    change = convert(ONE_MODEL)
    (question,) = [d for d in change.decisions if d.question]
    (model,) = [m for m in change.models if "revenue_events" in m.name]
    answered = convert(
        ONE_MODEL,
        answers={question.key: Answer(verify_option().label, ("order_id",))},
        descriptions={model.name: "Revenue events."},
    )

    emit(answered, context_for("jaffle_shop"), tmp_path)

    doc = yaml.safe_load((tmp_path / "models" / "staging" / f"{model.name}.yml").read_text())
    (model_doc,) = doc["models"]
    assert model_doc["description"] == "Revenue events."
    assert model_doc["columns"] == [{"name": "order_id", "tests": ["unique"]}]


def test_a_description_for_a_model_this_change_does_not_carry_is_refused() -> None:
    """The same refusal an orphan test gets, for the same reason: a
    description the walk computed and dropped would be a reader's own words
    silently discarded, with the screen still showing them.
    """
    from dbtw.core.assemble import UnknownModelError

    with pytest.raises(UnknownModelError, match="stg_ghost"):
        convert(ONE_MODEL, descriptions={"stg_ghost": "nothing describes this"})


def test_a_blank_description_is_not_recorded(tmp_path: Path) -> None:
    """Whitespace is not a description. Recording one would write
    `description: ''` into the reader's project, which claims the model was
    described and says nothing.
    """
    change, name = _described("   \n  ")
    assert change.descriptions == ()

    emit(change, context_for("jaffle_shop"), tmp_path)
    assert not (tmp_path / "models" / "staging" / f"{name}.yml").exists()


def test_a_description_that_would_stop_dbt_reading_the_project_is_refused() -> None:
    """dbt renders every schema .yml through Jinja before it parses the YAML,
    so a description is the one place this tool writes a reader's own prose
    into a file that is then executed.

    Checked against Jinja itself: the text below really is a
    `TemplateSyntaxError` once it is in a .yml, and the failure lands on `dbt
    parse` for the whole project rather than on this model.
    """
    import jinja2

    unclosed = "Revenue events {% if fresh %} as of today"
    with pytest.raises(jinja2.TemplateSyntaxError):
        jinja2.Environment().from_string(f"description: {unclosed}\n")

    with pytest.raises(JinjaInDescriptionError) as refused:
        convert(ONE_MODEL, descriptions={"stg_revenue_events": unclosed})
    assert "stg_revenue_events" in str(refused.value)


def test_an_unclosed_expression_in_a_description_is_refused() -> None:
    """`{{ doc('orders') }}` is ordinary dbt; `{{ doc('orders')` is the same
    project-wide failure with one bracket missing."""
    with pytest.raises(JinjaInDescriptionError):
        convert(ONE_MODEL, descriptions={"stg_revenue_events": "See {{ doc('orders')"})


def test_a_doc_reference_in_a_description_is_written_as_it_was_typed() -> None:
    """The refusal above must not take dbt's own feature with it: a balanced
    `{{ doc(...) }}` is how dbt descriptions reference a docs block, and a
    rule that refused it would be this tool forbidding the thing the field is
    for.
    """
    change = convert(ONE_MODEL, descriptions={"stg_revenue_events": "See {{ doc('orders') }}."})

    (described,) = change.descriptions
    assert described.text == "See {{ doc('orders') }}."


# What a description may carry of Jinja is exactly what dbt documents for the
# field: a `{{ doc('name') }}` reference to a docs block. Anything else between
# the braces is code dbt runs while it reads the project -- an undefined
# `var('x')` stops `dbt parse` for every model, and a defined one still makes a
# sentence about a model into something executed.
@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Daily totals {{ var('x') }} here", "{{ var('x') }}"),
        ("See {{ doc(name) }}.", "{{ doc(name) }}"),
        ("See {{ doc('orders') | upper }}.", "{{ doc('orders') | upper }}"),
        ("See {{ doc('my-block') }}.", "{{ doc('my-block') }}"),
        ("See {{ doc('orders\") }}.", "{{ doc('orders\") }}"),
        ("{% if fresh %}Fresh{% endif %} events", "{% if fresh %}"),
        ("Events {# note #} by day", "{# note #}"),
        ("See {{ doc('orders') }} and {{ this }}", "{{ this }}"),
        ("See {{ doc('orders')", "{{"),
        ("See doc('orders') }} here", "}}"),
    ],
)
def test_jinja_other_than_a_doc_reference_is_refused_and_quoted(text: str, found: str) -> None:
    with pytest.raises(JinjaInDescriptionError) as refused:
        convert(ONE_MODEL, descriptions={"stg_revenue_events": text})

    message = str(refused.value)
    assert "stg_revenue_events" in message
    assert found in message, "the refusal does not show the reader what it refused"
    assert "plain text" in message, "the refusal does not say what to write instead"


def test_a_refusal_points_at_doc_for_a_docs_block() -> None:
    """The one form that is allowed is the one a reader reaching for Jinja most
    likely wanted, so the refusal names it."""
    with pytest.raises(JinjaInDescriptionError) as refused:
        convert(ONE_MODEL, descriptions={"stg_revenue_events": "{{ var('x') }}"})

    assert "{{ doc('" in str(refused.value)


@pytest.mark.parametrize(
    "text",
    [
        "See {{ doc('orders') }}.",
        'See {{doc("orders")}}.',
        "{{   doc('orders_v2')   }} then {{ doc(\"_base\") }}",
    ],
)
def test_a_doc_reference_is_written_verbatim(text: str, tmp_path: Path) -> None:
    change, name = _described(text)

    (described,) = change.descriptions
    assert described.text == text

    emit(change, context_for("jaffle_shop"), tmp_path)
    doc = yaml.safe_load((tmp_path / "models" / "staging" / f"{name}.yml").read_text())
    assert doc["models"][0]["description"] == text


@pytest.mark.parametrize(
    "text", ["Totals per {region} } today", "A lone { brace", "A lone } brace", "100% sure"]
)
def test_single_braces_in_plain_text_are_accepted(text: str) -> None:
    change, _ = _described(text)

    (described,) = change.descriptions
    assert described.text == text
