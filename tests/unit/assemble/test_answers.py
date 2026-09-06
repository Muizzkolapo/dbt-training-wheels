import pytest
from tests.unit.assemble.helpers import context_for, state_with_variable

from dbtw.core.assemble import UnknownAnswerError, assemble
from dbtw.core.passes.types import Answer


def _variable_decision(change):
    (dec,) = [d for d in change.decisions if d.question.startswith("Is ")]
    return dec


def test_an_answer_overrides_the_inline_vars_default():
    """--inline-vars is the CLI's blanket answer to every variable question.
    A per-decision answer is the same question answered one variable at a
    time, and it wins where it is given."""
    state, ctx = state_with_variable(), context_for()
    baseline = assemble(state, ctx, inline_vars=False)
    key = _variable_decision(baseline).key

    answered = assemble(
        state, ctx, inline_vars=False, answers={key: Answer("inline the literal value")}
    )
    assert _variable_decision(answered).chosen == "inline the literal value"
    assert _variable_decision(baseline).chosen == "keep as a dbt var"


def test_an_answer_naming_an_option_that_was_not_offered_is_refused():
    """A label nobody offered cannot be honoured, and silently ignoring it
    would leave the screen showing an answer the run did not take."""
    state, ctx = state_with_variable(), context_for()
    key = _variable_decision(assemble(state, ctx)).key
    with pytest.raises(UnknownAnswerError):
        assemble(state, ctx, answers={key: Answer("delete the variable")})


def test_an_answer_for_an_unknown_decision_is_refused():
    """A stale key -- from an edited script, say -- must not pass silently."""
    state, ctx = state_with_variable(), context_for()
    with pytest.raises(UnknownAnswerError):
        assemble(state, ctx, answers={"assemble.variable.nope": Answer("keep as a dbt var")})
