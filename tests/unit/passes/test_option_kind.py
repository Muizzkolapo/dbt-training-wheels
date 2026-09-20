"""An option's stable identity, and the answer a consumer builds from it.

A label is prose the engine authors and rewrites: the same answer is spelled
"merge on a unique key, checked on every run" by the question the passes hand
out and "merge on order_id, checked on every run" by the Decision rebuilt once
that answer is applied. `Option.kind` is the half that does not move, so a
consumer records which answer a user gave without becoming a parser of our own
wording, and `answer_for` turns that kind back into the label the Decision in
front of it actually offers.
"""

import inspect
from typing import get_args

import pytest
from tests.unit.assemble.helpers import convert

from dbtw.core.assemble import ProjectChange
from dbtw.core.assemble.layers import intermediate_option, mart_option
from dbtw.core.context import LayerInfo
from dbtw.core.passes import (
    Answer,
    Decision,
    Option,
    OptionKind,
    answer_for,
    append_option,
    inline_option,
    merge_option,
    var_option,
    verify_option,
)

# Every Option the five factories can build, grouped by the factory that
# builds it, because the *number* of branches per factory has to be a claim
# something checks rather than a count kept by hand. A factory whose optional
# argument changes the option it returns has two; one taking no argument has
# one. `test_every_factory_branch_is_covered` derives that from each
# factory's own signature, so a factory gaining an argument fails until its
# second branch is listed here.
#
# Written this way because the flat tuple was wrong once already: the first
# version of this file missed `var_option()`'s no-name branch, and the
# `set(get_args(OptionKind))` check below did not notice, since the other
# branch still built the "var" kind. Dropping a branch has to fail on its
# own, not only when it takes a whole kind with it.
# One layer, standing for whichever of a project's own directories a layer
# option describes. Each layer factory has two branches: a model carrying no
# materialization of its own takes the layer's default, and one that carries
# its own keeps it wherever it is put -- and the effect has to say which,
# because an option promising "materialized as ephemeral" over a model that
# comes out incremental is a button that lies about what it does.
_LAYER = LayerInfo(name="marts", path="models/marts", prefix=None, materialization="table")

OPTIONS_BY_FACTORY = {
    append_option: (append_option(),),
    merge_option: (merge_option(), merge_option(("order_id",))),
    verify_option: (verify_option(), verify_option(("order_id",))),
    inline_option: (inline_option(),),
    var_option: (var_option(), var_option("cutoff")),
    intermediate_option: (intermediate_option(_LAYER), intermediate_option(_LAYER, "incremental")),
    mart_option: (mart_option(_LAYER), mart_option(_LAYER, "incremental")),
}
EVERY_OPTION = tuple(option for options in OPTIONS_BY_FACTORY.values() for option in options)


def test_every_factory_branch_is_covered():
    """`EVERY_OPTION` must hold every option each factory can build, and the
    parametrised invariants below are only worth what its coverage is worth.

    The expected count comes off the factory's signature rather than a list
    kept beside it: a factory taking an argument it can be called *without*
    returns a different option with and without it, so it has two branches.
    A factory whose argument is required has one -- it cannot be called the
    other way. Two hand-written lists agreeing with each other would prove
    nothing -- they would be wrong together, which is exactly how
    `var_option()`'s second branch went uncovered the first time.

    Read off defaults rather than off the presence of parameters, which is
    what it said until the layer factories arrived: those take a `LayerInfo`
    they cannot do without, and counting them as two branches would have
    demanded a call that does not exist.
    """
    for factory, options in OPTIONS_BY_FACTORY.items():
        optional = [
            p
            for p in inspect.signature(factory).parameters.values()
            if p.default is not inspect.Parameter.empty
        ]
        expected = 2 if optional else 1
        assert len(options) == expected, factory.__name__
        # And the branches must actually differ, or covering "both" is one
        # option listed twice. Compared whole rather than on `label`, because
        # which field carries the difference is the factory's business:
        # `merge_option` and `verify_option` vary the label and the columns
        # prompt, while `var_option` keeps one label ("keep as a dbt var") and
        # varies only `effect` and `plain`, naming the variable it was given.
        assert len(set(options)) == expected, factory.__name__


def test_every_factory_stamps_its_kind():
    assert append_option().kind == "append"
    assert merge_option().kind == "merge"
    assert merge_option(("order_id",)).kind == "merge"
    assert verify_option().kind == "merge_checked"
    assert verify_option(("order_id",)).kind == "merge_checked"
    assert inline_option().kind == "inline"
    assert var_option("cutoff").kind == "var"


def test_kind_survives_the_keyed_and_keyless_spellings():
    """The two spellings of one answer differ in label and in nothing else
    a consumer should have to care about. A UI that recorded 'the user chose
    the checked merge' must be able to say so against either."""
    assert verify_option().kind == verify_option(("order_id",)).kind
    assert verify_option().label != verify_option(("order_id",)).label
    assert merge_option().kind == merge_option(("order_id",)).kind
    assert merge_option().label != merge_option(("order_id",)).label


def test_every_kind_the_literal_names_is_built_by_some_factory():
    """`OptionKind` and the factories are two lists of the same set, and
    nothing else makes them agree. A value in the Literal that no factory
    builds is a kind a consumer can branch on and never reach; a factory
    building one the Literal does not name would not type-check. This also
    keeps `EVERY_OPTION` honest -- the parametrised invariants below assert
    nothing if it stops covering the set.
    """
    assert {option.kind for option in EVERY_OPTION} == set(get_args(OptionKind))


@pytest.mark.parametrize("option", EVERY_OPTION, ids=lambda o: f"{o.kind}-{o.label}")
def test_the_checked_kind_is_exactly_the_kind_that_declares_a_test(option):
    """`kind` and `declares_test` describe overlapping facts and are set
    independently, and two different readers use them: the assembler decides
    "this is the checked answer" from `declares_test` (eight read sites),
    while a consumer picking an answer decides it from `kind`. The first
    Option where they disagree is a screen saying "checked on every run"
    beside a model with no `unique` test in its .yml, or a test written for an
    answer the user was never shown.

    Both directions, because both failures are real: a `merge_checked` option
    that declares nothing, and any other option that declares something. This
    is not an argument for collapsing the two fields -- `declares_test` names
    *which* test, an axis documented to widen -- it is what keeps them from
    drifting while they are correlated.
    """
    if option.kind == "merge_checked":
        assert option.declares_test == "unique"
    else:
        assert option.declares_test == ""


# The four scripts below reach every site that builds an option tuple: the
# merge question (one-column and two-column keys) and the append question in
# `passes/tier2.py`, and the variable question in `assemble/assembler.py`.
# The three answered runs in `_option_sets_the_pipeline_builds` reach the
# other two, which only an applied answer produces.
_APPEND_SQL = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
_MERGE_SQL = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n"
)
_TWO_KEY_MERGE_SQL = (
    "MERGE INTO dim_d AS t USING stg_d AS s ON t.a = s.a AND t.b = s.b "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n"
)
_VARIABLE_SQL = (
    "DECLARE @cutoff DATE = '2024-01-01';\n"
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders WHERE d >= @cutoff;\n"
)


def _incremental_key(change: ProjectChange) -> str:
    """The key of the one incremental question in `change` -- the question
    whose options include the append answer, which the variable question's
    (inline/var) never do."""
    (dec,) = [d for d in change.decisions if any(o.kind == "append" for o in d.options)]
    return dec.key


# Two statements, the second reading what the first writes: the first model
# reads a raw table (staging, no question) and the second reads a model of
# ours and is read by nothing (the fork).
_LAYER_SQL = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders;\n"
    "INSERT INTO revenue_daily SELECT order_id, amount FROM revenue_events;\n"
)


def _option_sets_the_pipeline_builds() -> dict[str, list[tuple[str, tuple[Option, ...]]]]:
    """Every optioned Decision the real pipeline builds, per run.

    Derived by running conversions rather than by writing option tuples out
    here. Five sites build them and each is free to add an option; a list in
    this file would keep passing while the questions it claims to mirror
    changed underneath it. Two of those five shapes only exist after an
    answer is applied, so three of these runs send one.
    """
    pristine = {
        "append question": convert(_APPEND_SQL),
        "merge question, one-column key": convert(_MERGE_SQL),
        "merge question, two-column key": convert(_TWO_KEY_MERGE_SQL),
        "variable question": convert(_VARIABLE_SQL, dialect="tsql"),
        # The layer question, which only a project with both sides of the
        # fork can ask: a model that reads our models and is read by none of
        # them could be the mart or the step before one, and a project with
        # no intermediate layer has nowhere to put the second answer. Against
        # `three_layers` rather than the default fixture for exactly that
        # reason -- jaffle_shop has staging and marts and no intermediate, so
        # this question never arises there.
        "layer question": convert(_LAYER_SQL, project="three_layers"),
    }
    checked = Answer(verify_option().label, ("order_id",))
    appended = Answer(append_option().label)
    rebuilt = {
        "append upgraded to a checked merge": convert(
            _APPEND_SQL, answers={_incremental_key(pristine["append question"]): checked}
        ),
        "one-column merge downgraded to append": convert(
            _MERGE_SQL,
            answers={_incremental_key(pristine["merge question, one-column key"]): appended},
        ),
        # The shape with no checked option at all: dbt's unique test checks
        # one column, so a two-column key never offers it.
        "two-column merge downgraded to append": convert(
            _TWO_KEY_MERGE_SQL,
            answers={_incremental_key(pristine["merge question, two-column key"]): appended},
        ),
    }
    return {
        name: [(d.key, d.options) for d in change.decisions if d.options]
        for name, change in (pristine | rebuilt).items()
    }


def test_no_question_the_pipeline_builds_offers_two_options_of_one_kind():
    """The invariant `answer_for` rests on, checked against the questions the
    passes and the assembler actually build. `answer_for` refuses a duplicated
    kind rather than picking between them, so this is what says that refusal
    stays unreachable -- and it is derived from real Decisions, so a site that
    later adds a fourth option is checked rather than assumed.
    """
    by_run = _option_sets_the_pipeline_builds()
    for run, sets in by_run.items():
        assert sets, f"{run} produced no Decision with options, so it proves nothing"
        for key, options in sets:
            kinds = [option.kind for option in options]
            assert len(set(kinds)) == len(kinds), f"{run}: {key} offers {kinds}"
    # Non-vacuous: these runs have to have reached every kind, or the loop
    # above is asserting distinctness over a subset of the questions.
    reached = {option.kind for sets in by_run.values() for _, options in sets for option in options}
    assert reached == set(get_args(OptionKind))


def _question(*options):
    return Decision(
        key="tier2.append.x.sql:0",
        tier=2,
        action="a",
        reason="r",
        source_file="x.sql",
        line_start=1,
        line_end=1,
        question="q?",
        chosen=options[0].label,
        options=options,
    )


def test_answer_for_uses_the_label_this_decision_offers():
    """The point of the whole task: a consumer names the kind, and the
    label comes off the Decision in front of it -- keyed or keyless as that
    Decision happens to spell it."""
    keyless = _question(append_option(), merge_option(), verify_option())
    keyed = _question(merge_option(("order_id",)), append_option(), verify_option(("order_id",)))

    assert answer_for(keyless, "merge_checked", ("order_id",)).label == verify_option().label
    # The keyed spelling names its key already, so it takes no columns.
    assert answer_for(keyed, "merge_checked").label == verify_option(("order_id",)).label


def test_answer_for_carries_the_columns_through():
    keyless = _question(append_option(), merge_option(), verify_option())
    assert answer_for(keyless, "merge_checked", ("order_id",)).columns == ("order_id",)


def test_an_answer_to_an_option_that_settles_itself_carries_no_columns():
    """The other half of the pair above: nothing is invented to fill the
    field either, so a keyed answer is byte for byte what the engine's own
    gate expects."""
    keyed = _question(merge_option(("order_id",)), append_option(), verify_option(("order_id",)))
    assert answer_for(keyed, "merge_checked").columns == ()
    assert answer_for(keyed, "append").columns == ()


def test_columns_sent_to_an_option_that_names_its_own_key_are_refused():
    """`assemble` already refuses this ("that option takes no columns, so they
    would have been discarded"). `answer_for` must refuse it at the point the
    caller can still do something about it, rather than pass it on."""
    keyed = _question(merge_option(("order_id",)), append_option(), verify_option(("order_id",)))
    with pytest.raises(ValueError, match="takes no columns"):
        answer_for(keyed, "merge_checked", ("order_id",))
    assert answer_for(keyed, "merge_checked").label == verify_option(("order_id",)).label


def test_an_option_that_asks_for_columns_and_gets_none_is_refused():
    keyless = _question(append_option(), merge_option(), verify_option())
    with pytest.raises(ValueError, match="got no columns"):
        answer_for(keyless, "merge_checked")


def test_the_no_columns_refusal_names_the_prompt_the_option_asks_with():
    """A caller told only "no columns" has to guess what to collect. The
    option already carries the wording it asks for them in, and that wording
    is what a screen puts above the field -- so the refusal quotes it rather
    than inventing a second phrasing of the same requirement."""
    keyless = _question(append_option(), merge_option(), verify_option())
    with pytest.raises(ValueError) as refused:
        answer_for(keyless, "merge")
    assert merge_option().columns_prompt in str(refused.value)


def test_answer_for_refuses_a_kind_the_decision_does_not_offer():
    """Loud, not a silent fallback to the first option: a UI asking for an
    answer this question never offered is a UI bug, and it must surface as
    one rather than as an answer the user did not give."""
    only_append = _question(append_option())
    with pytest.raises(ValueError, match="merge_checked"):
        answer_for(only_append, "merge_checked", ("order_id",))


def test_the_unoffered_kind_refusal_names_what_is_on_offer():
    """The message has to be actionable from the caller's side, so it names
    the kinds this question does offer -- the vocabulary the caller should
    have picked from."""
    keyless = _question(append_option(), merge_option(), verify_option())
    with pytest.raises(ValueError) as refused:
        answer_for(keyless, "full_refresh")
    message = str(refused.value)
    assert "append, merge, merge_checked" in message
    assert keyless.key in message


def test_two_options_of_one_kind_are_refused_rather_than_resolved_by_order():
    """One kind is one answer, so a question offering two of a kind cannot be
    answered by kind at all. Returning the first would pick between them by
    the order they were built in -- a silent choice in a function whose every
    other exit says what is wrong, and one that would reach a user as an
    answer they did not give. No site builds such a question today; that is
    why this is refused rather than trusted.
    """
    doubled = _question(append_option(), merge_option(), merge_option(("order_id",)))
    with pytest.raises(ValueError) as refused:
        answer_for(doubled, "merge", ("order_id",))
    message = str(refused.value)
    assert "offers 2 'merge' options" in message
    # Both named, so whoever has to fix the question can see which two.
    assert merge_option().label in message
    assert merge_option(("order_id",)).label in message


def test_a_decision_with_no_options_refuses_every_kind():
    """A tier-1 Decision carries no options at all. Asking one for an answer
    is the same caller bug as asking for a kind it does not offer, and must
    not come back as an Answer naming nothing."""
    no_options = Decision(
        key="tier1.build.x.sql:0",
        tier=1,
        action="a",
        reason="r",
        source_file="x.sql",
        line_start=1,
        line_end=1,
    )
    with pytest.raises(ValueError, match="offers no 'append' option"):
        answer_for(no_options, "append")
