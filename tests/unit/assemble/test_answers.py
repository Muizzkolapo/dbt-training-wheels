import pytest
from tests.unit.assemble.helpers import context_for, state_with_variable

from dbtw.core.assemble import UnknownAnswerError, assemble
from dbtw.core.passes import Decision, PassState, append_option, merge_option
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


def test_an_answer_to_inline_changes_the_rendered_body_not_just_the_decision():
    """The Decision's `chosen` field is a claim about what the emitted model
    does. If the answer flips `chosen` to "inline the literal value" but the
    body still calls var('cutoff'), the claim is a lie the body doesn't back
    up, and the emitted project would carry an undeclared assumption about
    where its own value comes from. An answered run must match the
    equivalent flag-driven run byte for byte, not just agree on the label."""
    state, ctx = state_with_variable(), context_for()
    key = _variable_decision(assemble(state, ctx)).key

    answered = assemble(
        state, ctx, inline_vars=False, answers={key: Answer("inline the literal value")}
    )
    flag_driven = assemble(state, ctx, inline_vars=True)

    assert answered.models[0].body == flag_driven.models[0].body
    assert "'2024-01-01'" in answered.models[0].body
    assert "var('cutoff')" not in answered.models[0].body
    assert answered.variables == ()


def test_an_answer_to_keep_as_a_var_keeps_the_body_a_var_under_inline_vars():
    """The mirror of the above: an answer overriding a *global*
    --inline-vars=True back toward "keep as a dbt var" must leave the body
    calling var() and must keep the variable declared in change.variables --
    the direction nothing else exercises."""
    state, ctx = state_with_variable(), context_for()
    key = _variable_decision(assemble(state, ctx)).key

    answered = assemble(state, ctx, inline_vars=True, answers={key: Answer("keep as a dbt var")})

    assert "var('cutoff')" in answered.models[0].body
    assert "'2024-01-01'" not in answered.models[0].body
    assert [v.name for v in answered.variables] == ["cutoff"]


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


def test_an_answer_to_a_decision_this_run_cannot_apply_is_refused():
    """An inherited Decision from an earlier pass (e.g. tier2's append/merge
    incremental question) can carry a `question` and `options` too, but
    assemble() has no branch that consumes an answer for it -- only the
    variable questions this run itself builds are wired up (Task 3 widens
    this). Answering it must be refused exactly like a stale key, not
    silently accepted while leaving the Decision, and the model, unchanged."""
    inherited = Decision(
        key="tier2.append.e.sql:0",
        tier=2,
        action="INSERT INTO orders became an append incremental model",
        reason="an INSERT...SELECT with no MERGE evidence defaults to append",
        source_file="e.sql",
        line_start=1,
        line_end=1,
        question="Should rows be appended on every run, or deduplicated on a unique key?",
        chosen=append_option().label,
        options=(append_option(), merge_option()),
    )
    state = PassState(pending=(), drafts=(), decisions=(inherited,), dialect=None)
    ctx = context_for()
    with pytest.raises(UnknownAnswerError):
        assemble(state, ctx, answers={inherited.key: Answer(merge_option().label)})
