"""The plain register for every Decision saying a unique key was not applied.

Five Decisions record the same thing from five directions: something named a
column to match rows on, and this conversion did not use it. Two of them are
reachable by answering a question in the browser --
`assemble.unique_key_not_selected.<table>` and
`assemble.unique_key_ambiguous.<table>` -- and the walk renders them beside
the question they answer, which is the state a reader meets them in: they
typed a column, the screen shows "append every row -- chosen", and this
Decision is the only thing that says why. The other three answer for
`--unique-key` and reach the report rather than a screen. All five are one
pattern, and spec 11.4(b) wants both registers on it, written by the engine.

What a guard here has to decide is whether a sentence *asserts* the claim,
not whether its words are present: a register saying "customer_id is now what
revenue_events matches rows on" carries every word of the claim it reverses.
`tests/unit/register_claims.py` is the module that decides that, and its
docstring says what it can and cannot see. The claims below are built from it
with this file's own fixture names, and the discipline tests at the bottom
are the local copies of the ones that hold the shared claims honest: every
claim accepts a text that makes it, every group of a multi-group claim is the
difference between rejection and acceptance, every claim names a subject, and
a reversal written out of the claim's own words is rejected.
"""

from __future__ import annotations

import pytest
from tests.unit.assemble.helpers import convert
from tests.unit.passes.test_plain_register import JARGON
from tests.unit.register_claims import Claim, names, scoped_clauses

from dbtw.core.passes.types import Answer

ONE_APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
TWO_APPENDS = ONE_APPEND + "INSERT INTO page_views SELECT order_id, ts FROM stg_views;\n"
ONE_MERGE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n"
)
# A quoted output name. `Order_Id` and `order_id` are the same column or two
# different ones depending on how the database folds case, and the SQL text
# does not say which -- which is the whole of the ambiguous branch.
QUOTED = 'INSERT INTO revenue_events SELECT order_id AS "Order_Id", amount FROM raw.orders;\n'
NO_INCREMENTAL = "CREATE TABLE dim_people AS SELECT id, name FROM raw_people;\n"


def _question(change, table: str):
    (decision,) = [d for d in change.decisions if d.question and table in d.action]
    return decision


def _decision(change, key: str):
    (decision,) = [d for d in change.decisions if d.key == key]
    return decision


def _not_selected():
    key = _question(convert(ONE_APPEND), "revenue_events").key
    change = convert(ONE_APPEND, answers={key: Answer("merge on a unique key", ("customer_id",))})
    return (
        change,
        _decision(change, "assemble.unique_key_not_selected.revenue_events"),
        ("customer_id",),
    )


def _ambiguous():
    key = _question(convert(QUOTED), "revenue_events").key
    change = convert(QUOTED, answers={key: Answer("merge on a unique key", ("order_id",))})
    return change, _decision(change, "assemble.unique_key_ambiguous.revenue_events"), ("order_id",)


def _ignored():
    change = convert(ONE_MERGE, unique_key=("order_id",))
    return change, _decision(change, "assemble.unique_key_ignored.dim_c"), ("order_id",)


def _overridden_taken():
    """The flag displaced by an answer this conversion then applied."""
    key = _question(convert(TWO_APPENDS), "page_views").key
    change = convert(
        TWO_APPENDS, unique_key=("order_id",), answers={key: Answer("append every row")}
    )
    return change, _decision(change, "assemble.unique_key_overridden.page_views"), ("order_id",)


def _overridden_declined():
    """The flag displaced by an answer the body check then declined, so
    neither input reached the model."""
    key = _question(convert(ONE_APPEND), "revenue_events").key
    change = convert(
        ONE_APPEND,
        unique_key=("order_id",),
        answers={key: Answer("merge on a unique key", ("customer_id",))},
    )
    return (
        change,
        _decision(change, "assemble.unique_key_overridden.revenue_events"),
        (
            "order_id",
            "customer_id",
        ),
    )


def _unused():
    change = convert(NO_INCREMENTAL, unique_key=("order_id",))
    return change, _decision(change, "assemble.unique_key_unused"), ("order_id",)


CASES = {
    "not_selected": _not_selected,
    "ambiguous": _ambiguous,
    "ignored": _ignored,
    "overridden (answer taken)": _overridden_taken,
    "overridden (answer declined)": _overridden_declined,
    "unused": _unused,
}


# --- the claims, built from the shared machinery with this file's names


def _key_not_used(column: str, conflicting: tuple[str, ...] = ()) -> Claim:
    """The key named was not used to match rows on.

    A denial, so the negation is one of its own groups: a clause carrying the
    column and a matching word but no negation is the reversal, and it must
    not count as evidence for the refusal.
    """
    return Claim(
        name=f"{column} was not used to match rows on",
        groups=(
            ("no", "not", "nothing", "never"),
            ("match", "matches", "matching", "matched", "used", "applied", "taken"),
        ),
        subject=(column,),
        conflicting=conflicting,
        affirmative=False,
    )


def _still_adds_every_row(table: str) -> Claim:
    """The model goes on adding everything it selects, every run.

    What the reader is left with when the key is declined, and the half a
    register can drop while still sounding like a refusal.
    """
    return Claim(
        name=f"{table} goes on adding every row",
        groups=(
            ("adds", "adding", "added"),
            ("every row", "everything", "all of"),
        ),
        subject=(table,),
    )


def _still_matches_on(table: str, column: str, conflicting: tuple[str, ...]) -> Claim:
    """The model goes on matching rows on the key it already had."""
    return Claim(
        name=f"{table} goes on matching rows on {column}",
        groups=(
            ("match", "matches", "matching", "matched"),
            ("goes on", "still", "keeps", "keeping", "kept", "carries on"),
        ),
        subject=(column,),
        conflicting=conflicting,
    )


NOT_USED = {
    "not_selected": _key_not_used("customer_id"),
    "ambiguous": _key_not_used("order_id"),
    "ignored": _key_not_used("order_id", conflicting=("id",)),
    "overridden (answer taken)": _key_not_used("order_id"),
    "overridden (answer declined)": _key_not_used("order_id"),
    "unused": _key_not_used("order_id"),
}

# What the model does instead, for the cases that have a model to say it of.
# `unused` is deliberately absent: no model in that change is incremental at
# all, which is the whole of what it records.
OUTCOME = {
    "not_selected": _still_adds_every_row("revenue_events"),
    "ambiguous": _still_adds_every_row("revenue_events"),
    "ignored": _still_matches_on("dim_c", "id", conflicting=("order_id",)),
    "overridden (answer taken)": _still_adds_every_row("page_views"),
    "overridden (answer declined)": _still_adds_every_row("revenue_events"),
}


def _draft(final_name: str) -> str:
    """The pre-rename name these Decisions spell, from the final one.

    Every model in these fixtures is renamed by the jaffle_shop layer's `stg_`
    prefix, and the Decisions here name the draft -- the same spelling their
    own `action` uses, so the action and both registers name one table.
    """
    return final_name.removeprefix("stg_")


# A verb that says rows are matched. Not a `Claim`: what is asked here is not
# "does this text make a claim" but "does it make one it must not", so the
# scan runs over every clause and reports the ones that do.
_MATCHING = ("match", "matches", "matching", "matched")


def _matched_on(text: str, keys: tuple[str, ...]) -> list[str]:
    """Every clause of `text` that affirmatively says rows are matched on one
    of `keys`.

    Affirmative only, which is the whole difficulty. "nothing was set up to
    match rows on it" names a key beside a matching verb and asserts the
    opposite, and it is the sentence these registers exist to write -- a scan
    that could not tell it from the assertion would reject every correct
    register here. Governed clauses are skipped for that reason, on the same
    forward-scoped, sentence-bounded rule `register_claims` uses everywhere
    else.
    """
    offending: list[str] = []
    for clause, governed in scoped_clauses(text):
        if governed or not any(names(clause, verb) for verb in _MATCHING):
            continue
        offending.extend(f"{key}: {clause}" for key in keys if names(clause, key))
    return offending


# --- the register itself


@pytest.mark.parametrize("case", CASES)
def test_every_declined_unique_key_carries_a_plain_reason(case: str) -> None:
    """Spec 11.4(b): both registers, engine-owned. An empty `plain_reason`
    renders as the dbt sentence alone -- and for the two cases the walk can
    reach, that dbt sentence is the only thing on screen saying why the answer
    the reader gave did not take effect."""
    _change, decision, _asked = CASES[case]()
    assert decision.plain_reason.strip(), f"{case}: {decision.key} carries no plain reason"
    assert decision.plain_reason != decision.reason, case


@pytest.mark.parametrize("case", CASES)
def test_the_plain_reason_uses_no_dbt_vocabulary(case: str) -> None:
    _change, decision, _asked = CASES[case]()
    plain = decision.plain_reason.lower()
    found = [term for term in JARGON if term in plain]
    assert not found, f"{case}: the plain reason uses {found}: {decision.plain_reason}"


@pytest.mark.parametrize("case", CASES)
def test_the_plain_reason_says_the_key_it_names_was_not_used(case: str) -> None:
    """The one thing a reader has to take away: the column that was named is
    not what this model matches rows on. A register that explained the
    mechanics and never said so leaves them believing the opposite of the file
    written beside it."""
    _change, decision, _asked = CASES[case]()
    assert NOT_USED[case].asserted_in(decision.plain_reason), f"{case}: {decision.plain_reason}"


@pytest.mark.parametrize("case", OUTCOME)
def test_the_plain_reason_says_what_the_model_does_instead(case: str) -> None:
    """Not what was refused -- what now happens. "The key was not applied" is
    a fact about this tool; "it goes on adding every row it finds" is a fact
    about the table the reader will have tomorrow, and it is the half that
    tells them whether to care."""
    _change, decision, _asked = CASES[case]()
    assert OUTCOME[case].asserted_in(decision.plain_reason), f"{case}: {decision.plain_reason}"


@pytest.mark.parametrize("case", OUTCOME)
def test_the_plain_reason_agrees_with_the_model_this_run_produced(case: str) -> None:
    """Read off the model, not off the branch that was expected to run. A
    Decision saying a table goes on adding every row, beside a model that came
    out matching on a key, is the contradiction this project does not ship --
    and it is the failure a register copied between two of these branches
    would produce."""
    change, decision, _asked = CASES[case]()
    draft = decision.key.rsplit(".", 1)[1]
    (model,) = [m for m in change.models if _draft(m.name) == draft]
    adds_every_row = _still_adds_every_row(draft).asserted_in(decision.plain_reason)
    assert adds_every_row is not bool(model.unique_key), (
        f"{case}: {model.name} came out with unique_key={model.unique_key!r} and the "
        f"plain reason {'says' if adds_every_row else 'does not say'} it adds every row"
    )


@pytest.mark.parametrize("case", OUTCOME)
def test_no_clause_says_the_model_matches_on_a_key_it_did_not_come_out_with(case: str) -> None:
    """The property, rather than one sentence of it.

    `ignored`'s register names the key it kept twice -- once introducing it
    and once asserting it -- and only the second naming sits in a clause the
    claim above can see. Swapping the first one for the flag's key ships a
    record whose opening sentence says the model matches on one column and
    whose closing sentence says it matches on another, and the model file
    written beside it agrees with neither. That mutation passed the whole
    suite.

    So this asks the register as a whole, in every case rather than in the
    one that was found: no clause of it may affirmatively pair a matching
    verb with a column this model did not come out keyed on. It reads
    `model.unique_key` off the run, so it needs no table of expected
    outcomes, and it covers a register copied between two of these branches
    as readily as one edited in place.
    """
    change, decision, asked = CASES[case]()
    draft = decision.key.rsplit(".", 1)[1]
    (model,) = [m for m in change.models if _draft(m.name) == draft]

    # `asked` is this fixture's own input, and this is what stops it drifting
    # away from the fixture it is written beside: the Decision names what was
    # asked for, so a key listed here that nothing asked for fails.
    for key in asked:
        assert key in decision.action, f"{case}: nothing asked for {key}"

    declined = tuple(key for key in asked if key not in model.unique_key)
    offending = _matched_on(decision.plain_reason, declined)
    assert not offending, (
        f"{case}: {model.name} came out with unique_key={model.unique_key!r} and the plain "
        f"reason says rows are matched on {offending}"
    )


def test_the_matching_scan_sees_a_register_that_names_the_wrong_key() -> None:
    """Both directions, because either one alone is free.

    The first is the sentence the surviving mutation produced. The second is
    the denial every declined register has to be allowed to write, and a scan
    that rejected it would have been satisfied by deleting the refusal.
    """
    swapped = (
        "The statement this model came from already names the column dim_c matches rows "
        "on, which is order_id."
    )
    assert _matched_on(swapped, ("order_id",))

    denial = (
        "revenue_events does not select customer_id, so nothing was set up to match rows on it."
    )
    assert _matched_on(denial, ("customer_id",)) == []


# --- the discipline that holds the claims above honest


ACCEPTED = {
    _key_not_used("customer_id"): (
        "revenue_events does not select customer_id, so nothing was set up to match rows on it."
    ),
    _still_adds_every_row("revenue_events"): (
        "revenue_events goes on adding every row it finds, every run."
    ),
    _still_matches_on("dim_c", "id", conflicting=("order_id",)): (
        "So dim_c goes on matching rows on id."
    ),
}

# One text per group of each claim: a sentence satisfying the claim's OTHER
# groups and not this one. Dropping the group has to turn a rejection into an
# acceptance, which is what "this group is load-bearing" means.
WITNESSES = {
    _key_not_used("customer_id"): {
        0: "revenue_events matches rows on customer_id.",
        1: "revenue_events does not select customer_id.",
    },
    _still_adds_every_row("revenue_events"): {
        0: "revenue_events takes every row it finds.",
        1: "revenue_events goes on adding what the query gives it.",
    },
    _still_matches_on("dim_c", "id", conflicting=("order_id",)): {
        0: "This conversion still has id.",
        1: "This conversion matches rows on id.",
    },
}


def _shape(claim: Claim) -> tuple[tuple[tuple[str, ...], ...], bool]:
    """What the matcher actually runs on, with the subject set aside."""
    return claim.groups, claim.affirmative


def test_every_claim_this_file_makes_accepts_a_text_that_makes_it() -> None:
    """A matcher that rejects everything passes every assertion above."""
    for claim, text in ACCEPTED.items():
        assert claim.asserted_in(text), f"{claim.name}: {text}"


def test_every_group_of_every_claim_this_file_makes_is_load_bearing() -> None:
    """Each group is the difference between rejection and acceptance, which is
    the direction that carries meaning: dropping a conjunct can only grow the
    accepting set, so asserting that the weakened claim still accepts what the
    full one accepted could never fail."""
    assert set(WITNESSES) == set(ACCEPTED)
    # Every matching shape this file uses has a positive control. The claims
    # above differ from these only in which column and table they are about;
    # the groups and the polarity are what the matcher runs on, so a shape
    # nothing here was shown accepting or rejecting is a shape nothing checks.
    assert {_shape(claim) for claim in (*NOT_USED.values(), *OUTCOME.values())} == {
        _shape(claim) for claim in ACCEPTED
    }
    for claim, witnesses in WITNESSES.items():
        assert len(claim.groups) > 1, claim.name
        assert sorted(witnesses) == list(range(len(claim.groups))), claim.name
        for dropped, witness in witnesses.items():
            weakened = Claim(
                name=claim.name,
                groups=tuple(g for i, g in enumerate(claim.groups) if i != dropped),
                subject=claim.subject,
                conflicting=claim.conflicting,
                affirmative=claim.affirmative,
            )
            assert not claim.asserted_in(witness), f"{claim.name}[{dropped}]: {witness}"
            assert weakened.asserted_in(witness), f"{claim.name}[{dropped}]: {witness}"


def test_every_claim_this_file_makes_names_a_subject() -> None:
    """A claim with no subject is a word search over the whole register."""
    for claim in (*NOT_USED.values(), *OUTCOME.values()):
        assert claim.subject, claim.name


def test_a_register_reversed_out_of_the_claim_s_own_words_is_rejected() -> None:
    """The prose these guards would accept if they only looked for words. Each
    sentence below carries every word of the claim it reverses and asserts the
    opposite, and each is one edit away from the real wording."""
    # Says the key WAS applied, and says the table stopped adding every row.
    # The first has no negation to make it a refusal; the second's negation
    # governs the claim that follows it.
    reversed_decline = (
        "revenue_events selects customer_id, so customer_id is what it matches rows on "
        "from here. revenue_events no longer adds every row it finds."
    )
    assert not NOT_USED["not_selected"].asserted_in(reversed_decline)
    assert not OUTCOME["not_selected"].asserted_in(reversed_decline)

    # Names both keys in one clause, so it is evidence about neither: the
    # reader cannot tell from it which key dim_c came out matching on.
    both_keys = "So dim_c goes on matching rows on order_id, and id was not used here."
    assert not OUTCOME["ignored"].asserted_in(both_keys)
