"""Which layer a model belongs in, and who decides.

`role_for` reads this conversion's own dependency graph: a model that reads
no model of ours is staging, one that other models read is intermediate.
Neither is a guess. The third branch is -- it reads our models and nothing
reads it, which is what the end of a pipeline looks like and equally what an
intermediate model looks like the day before the mart that will read it is
written. That one fork is the whole subject of this file.
"""

from __future__ import annotations

import pytest
from tests.unit.assemble.helpers import convert

from dbtw.core.assemble import UnknownAnswerError
from dbtw.core.passes import Answer, answer_for

# The first model reads a raw table; the second reads the first and is read by
# nothing. So the first is staging by what it reads, and the second is the one
# this engine is guessing about.
CHAIN = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders;\n"
    "INSERT INTO revenue_daily SELECT order_id, amount FROM revenue_events;\n"
)

THREE = "three_layers"


def _layer_question(change):  # type: ignore[no-untyped-def]
    return next(d for d in change.decisions if d.key.startswith("assemble.layer."))


def test_only_the_model_the_engine_is_guessing_about_is_asked_about() -> None:
    """A question per model would put a screen in front of placements nothing
    is unsure of -- and on a ten-model script, ten screens on a walk whose
    claim is that it asks what matters.
    """
    change = convert(CHAIN, project=THREE)

    asked = [d.key for d in change.decisions if d.key.startswith("assemble.layer.")]

    assert asked == ["assemble.layer.revenue_daily"]
    (staged,) = [m for m in change.models if m.name.endswith("revenue_events")]
    assert staged.layer == "staging", "the model that reads raw is not a guess"


def test_the_engine_proposes_the_mart_and_says_so() -> None:
    change = convert(CHAIN, project=THREE)

    question = _layer_question(change)

    assert question.question, "the fork is asked, not settled in silence"
    assert question.chosen == "put it in marts"
    assert [option.kind for option in question.options] == ["intermediate", "mart"]
    (leaf,) = [m for m in change.models if m.name.endswith("revenue_daily")]
    assert leaf.layer == "marts"
    assert leaf.path == "models/marts/revenue_daily.sql"


def test_answering_intermediate_moves_the_model_and_its_file() -> None:
    """The answer has to reach the path, not only the Decision. A screen
    saying "put it in intermediate" over a file still written to models/marts
    is the tool disagreeing with itself about what it did.
    """
    pristine = convert(CHAIN, project=THREE)
    question = _layer_question(pristine)

    change = convert(
        CHAIN, project=THREE, answers={question.key: answer_for(question, "intermediate")}
    )

    (leaf,) = [m for m in change.models if m.name.endswith("revenue_daily")]
    assert leaf.layer == "intermediate"
    assert leaf.path == "models/intermediate/revenue_daily.sql"
    assert _layer_question(change).chosen == "put it in intermediate"


def test_a_project_with_nowhere_to_move_it_is_not_asked() -> None:
    """jaffle_shop keeps its marts at the model-path root and has no
    intermediate layer at all.

    Offering to move a model there would promise a placement this conversion
    cannot make: `_resolve_layer` would fall back somewhere else and record a
    Decision saying so, and the reader would have chosen one thing and been
    given another.
    """
    change = convert(CHAIN, project="jaffle_shop")

    assert not [d for d in change.decisions if d.key.startswith("assemble.layer.")]
    assert change.models, "the conversion still happens; it just asks nothing"


def test_an_answer_naming_a_layer_this_question_never_offered_is_refused() -> None:
    pristine = convert(CHAIN, project=THREE)
    question = _layer_question(pristine)

    with pytest.raises(ValueError):
        answer_for(question, "append")

    with pytest.raises(UnknownAnswerError):
        convert(CHAIN, project=THREE, answers={question.key: Answer("put it in nowhere")})


def test_the_option_says_the_materialization_the_model_will_actually_take() -> None:
    """A layer's materialization is its *default*, and a model carrying one
    of its own keeps it wherever it is put.

    The intermediate layer here defaults to ephemeral, and this model is
    incremental because of the question two screens earlier. An option
    promising "materialized as ephemeral" over a model that comes out
    incremental is a button that lies about what it does.
    """
    change = convert(CHAIN, project=THREE)
    question = _layer_question(change)

    (intermediate,) = [o for o in question.options if o.kind == "intermediate"]

    assert "ephemeral" not in intermediate.effect
    assert "incremental" in intermediate.effect
    answered = convert(
        CHAIN, project=THREE, answers={question.key: answer_for(question, "intermediate")}
    )
    (leaf,) = [m for m in answered.models if m.name.endswith("revenue_daily")]
    assert leaf.materialization == "incremental"


def test_a_layer_question_for_a_dropped_draft_is_not_answerable() -> None:
    """Two drafts can resolve to one final name, and only one is written.

    A question collected for the draft that was dropped is a question about a
    model this change does not carry, and registering it would let an answer
    validate against a placement nothing would apply -- the same rule the
    incremental questions follow, for the same reason.
    """
    twice = (
        "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders;\n"
        "INSERT INTO revenue_daily SELECT order_id, amount FROM revenue_events;\n"
        "INSERT INTO revenue_daily SELECT order_id, amount FROM revenue_events;\n"
    )
    change = convert(twice, project=THREE)

    written = {model.name for model in change.models}
    asked = [d.key for d in change.decisions if d.key.startswith("assemble.layer.")]

    for key in asked:
        named = key.removeprefix("assemble.layer.")
        assert any(name.endswith(named) for name in written), (
            f"{key} asks about a model this change does not write"
        )
