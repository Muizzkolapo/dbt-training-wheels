"""An option's stable identity, and the answer a consumer builds from it.

A label is prose the engine authors and rewrites: the same answer is spelled
"merge on a unique key, checked on every run" by the question the passes hand
out and "merge on order_id, checked on every run" by the Decision rebuilt once
that answer is applied. `Option.kind` is the half that does not move, so a
consumer records which answer a user gave without becoming a parser of our own
wording, and `answer_for` turns that kind back into the label the Decision in
front of it actually offers.
"""

import pytest

from dbtw.core.passes import (
    Decision,
    answer_for,
    append_option,
    inline_option,
    merge_option,
    var_option,
    verify_option,
)


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


def test_the_kinds_one_question_offers_are_distinct():
    """`answer_for` resolves a kind to one option, so two options on the same
    question sharing a kind would make the answer it returns depend on the
    order they were built in."""
    for options in (
        (append_option(), merge_option(), verify_option()),
        (merge_option(("order_id",)), append_option(), verify_option(("order_id",))),
        (inline_option(), var_option("cutoff")),
    ):
        kinds = [option.kind for option in options]
        assert len(set(kinds)) == len(kinds), kinds


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
