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
        ("Events {% if fresh", "{%"),
        ("Events {# note", "{#"),
        ("See {{ doc('pkg', name) }}.", "{{ doc('pkg', name) }}"),
        ("See {{ doc('a', 'b', 'c') }}.", "{{ doc('a', 'b', 'c') }}"),
        ("See {{ doc('pkg' 'orders') }}.", "{{ doc('pkg' 'orders') }}"),
    ],
)
def test_jinja_other_than_a_doc_reference_is_refused_and_quoted(text: str, found: str) -> None:
    """The quote is checked in its delimiters, which the fixed parts of the
    message never use -- so a refusal that quoted the wrong part of what the
    reader typed, or nothing, fails here."""
    with pytest.raises(JinjaInDescriptionError) as refused:
        convert(ONE_MODEL, descriptions={"stg_revenue_events": text})

    message = str(refused.value)
    assert message.startswith(f"The description of stg_revenue_events has “{found}”")


def test_the_refusal_of_an_expression_says_what_why_and_what_to_do() -> None:
    """Exact, because this is the text the reader meets on the describe screen."""
    with pytest.raises(JinjaInDescriptionError) as refused:
        convert(ONE_MODEL, descriptions={"stg_revenue_events": "Totals {{ var('x') }}"})

    assert str(refused.value) == (
        "The description of stg_revenue_events has “{{ var('x') }}”. "
        "dbt reads the text between {{ and }} as code. "
        "dbt runs this code when it reads the project. "
        "Code that dbt cannot run stops dbt for the whole project, not only for this model. "
        "Two examples are a var() that has no value and a name that dbt does not know. "
        "Write the description as plain text, without the braces. "
        "To show a docs block, write {{ doc('block_name') }} with no other text between "
        "the braces. A docs block is a longer description in a .md file in the project."
    )


def test_the_refusal_of_an_unclosed_tag_says_it_is_not_closed() -> None:
    with pytest.raises(JinjaInDescriptionError) as refused:
        convert(ONE_MODEL, descriptions={"stg_revenue_events": "Events {% if fresh"})

    assert str(refused.value) == (
        "The description of stg_revenue_events has “{%”, and no “%}” closes it. "
        "dbt reads the text between {% and %} as an instruction, for example an if "
        "or a loop. dbt runs the instruction when it reads the project. "
        "An instruction that dbt cannot run stops dbt for the whole project, not only "
        "for this model. "
        "Write the description as plain text, without the braces. "
        "To show a docs block, write {{ doc('block_name') }} with no other text between "
        "the braces. A docs block is a longer description in a .md file in the project."
    )


def test_the_refusal_of_a_comment_says_the_words_would_not_show() -> None:
    with pytest.raises(JinjaInDescriptionError) as refused:
        convert(ONE_MODEL, descriptions={"stg_revenue_events": "Events {# note #}"})

    assert str(refused.value) == (
        "The description of stg_revenue_events has “{# note #}”. "
        "dbt reads the text between {# and #} as a hidden note. "
        "dbt deletes the note, so the words do not show in the documentation. "
        "A note that is not closed stops dbt for the whole project. "
        "Write the description as plain text, without the braces. "
        "To show a docs block, write {{ doc('block_name') }} with no other text between "
        "the braces. A docs block is a longer description in a .md file in the project."
    )


ACCEPTED_DOC_REFERENCES = [
    "See {{ doc('orders') }}.",
    'See {{doc("orders")}}.',
    "{{   doc('orders_v2')   }} then {{ doc(\"_base\") }}",
    "See {{ doc( 'orders' ) }}.",
    "See {{- doc('orders') -}}.",
    "See {{ doc('my_package', 'orders') }}.",
    'See {{ doc("my_package","orders") }}.',
]


@pytest.mark.parametrize("text", ACCEPTED_DOC_REFERENCES)
def test_a_doc_reference_is_written_verbatim(text: str, tmp_path: Path) -> None:
    change, name = _described(text)

    (described,) = change.descriptions
    assert described.text == text

    emit(change, context_for("jaffle_shop"), tmp_path)
    doc = yaml.safe_load((tmp_path / "models" / "staging" / f"{name}.yml").read_text())
    assert doc["models"][0]["description"] == text


@pytest.mark.parametrize("text", ACCEPTED_DOC_REFERENCES)
def test_every_accepted_doc_reference_renders_under_jinja(text: str) -> None:
    """Accepted means dbt can render it: checked against Jinja itself, with a
    `doc` that takes the one-name and the package-and-name forms dbt takes."""
    import jinja2

    def doc(*names: str) -> str:
        assert len(names) in (1, 2)
        return "DOC"

    rendered = jinja2.Environment().from_string(text).render(doc=doc)
    assert "DOC" in rendered
    assert "{{" not in rendered


PLAIN_TEXT = [
    "Totals per {region} } today",
    "A lone { brace",
    "A lone } brace",
    "100% sure",
    'Payload: {"a": {"b": 1}}',
    "Ends with }} here",
    "Up 50%} on last week",
    "Issue #} closed",
]


@pytest.mark.parametrize("text", PLAIN_TEXT)
def test_plain_text_with_braces_and_stray_closers_is_accepted(text: str) -> None:
    change, _ = _described(text)

    (described,) = change.descriptions
    assert described.text == text


@pytest.mark.parametrize("text", PLAIN_TEXT)
def test_accepted_plain_text_renders_unchanged_under_jinja(text: str) -> None:
    """The reason a stray closer is accepted: Jinja reads it as plain text."""
    import jinja2

    assert jinja2.Environment().from_string(text).render() == text
