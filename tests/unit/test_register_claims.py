"""The texts that got past this branch's guards, held as pins.

These are regression pins, not coverage. Each is a text that defeated a
specific version of the claim matching in `tests/unit/register_claims.py` and
survived the whole suite at the commit it was written against. Pinning them
says "this exact defeat does not work any more"; it does not say the class is
closed, and `register_claims`'s own docstring is explicit about the class that
stays open.

Four came from a blind review (R1, R2, R4, and R3 which lives in
test_rename_cutover.py beside the assertion it defeated). R5 came from
mutating the fix for R4, which is the order this should happen in: a fix that
has not been attacked is a fix that has not been tested.

The distinction is Task 3's: a guard that has only ever met the words already
inside it has never been tested. These are the words that were not inside it.

R1 is quoted from the review with its elisions filled by the shortest
connective text that makes it a whole paragraph -- every fragment the review
quoted appears here verbatim, and the filled spans carry no claim words, so
they cannot be what the matcher reacts to. R2 and R4 are verbatim, with the
review's bold markers removed and R4's `{condition}` substituted with the
fixture's own condition.
"""

from __future__ import annotations

from tests.unit.register_claims import (
    LEFT_ALONE,
    NARROWING_IS_MANUAL,
    NEVER_SELECTED,
    REPOINTING_IS_MANUAL,
    RESCAN,
    STILL_READ,
    Claim,
    clauses,
)

# R1 -- the rename's plain register, with the Django `RenameModel`
# misconception restated by the tool as fact. It survived commit c235102
# because `_affirmed_of` inspected only the span BETWEEN the table name and
# the claim word, so "do not" sitting before `stg_events` was invisible.
R1_RENAME_RESTATES_THE_MISCONCEPTION = (
    "Renaming moves the table for you. Everything that was reading events is repointed as "
    "part of this, and then it all points at stg_events instead — events stays exactly "
    "where it was, now under the name stg_events. You do not have to point anything at "
    "stg_events yourself, and there is nothing further to do."
)

# R2 -- the filter caveat's closing sentence, in both registers. It survived
# because the filter test file ran `_BY_HAND` through a bare substring search
# while the rename file ran the same words through the tempered helper: the
# pattern was fixed in one file and shipped unfixed in its sibling.
R2_FILTER_CLAIMS_THE_CONVERSION_ALREADY_DID_IT = (
    "Narrowing the filter, or guarding it, is not a change anyone has to make by hand -- "
    "this conversion has already done it."
)

# R4 -- the filter caveat's rescan sentence, reversed. No negation anywhere:
# the contrast carries the reversal, and both halves of the conjunction are
# present, so a "one word from each half in one sentence" check reads it as
# the claim it denies.
R4_FILTER_CLAIMS_DBT_NARROWS_IT = (
    "So dbt narrows it for you — every run evaluates occurred_at > '2024-01-01' over only "
    "what arrived since the last run rather than over the whole of this model's source, so "
    "the work re-done stays small."
)


# R5 -- not from the review. Found by mutating the fix for R4: the filter
# caveat closes with an illustration in brackets that carries every word of the
# claim the sentence before it makes, so gutting that claim and leaving the
# illustration standing still satisfied the check. Recorded here with the other
# four because its provenance does not change what it is -- a text that once
# passed a guard it should not have.
R5_FILTER_KEEPS_ONLY_THE_ILLUSTRATION = (
    "and a row that does not match is left where it is, however late it arrives (if the "
    "filtering compares a date, a row turning up afterwards carrying an earlier one never "
    "enters stg_events)"
)


# --- the five defeats, each rejected by the claim it was written against


def test_a_negation_before_the_subject_is_not_evidence_for_the_claim():
    """R1's manual-work sentence puts "do not" ahead of the table name, where
    a between-the-tokens window cannot see it. Clause scope sees it wherever
    it sits."""
    assert not REPOINTING_IS_MANUAL.asserted_in(R1_RENAME_RESTATES_THE_MISCONCEPTION)


def test_a_claim_about_one_table_made_in_a_clause_naming_both_is_not_evidence():
    """R1's other half: "events stays exactly where it was, now under the name
    stg_events" is affirmative and carries the claim word, and is a statement
    about a rename rather than about a table left alone. A clause naming both
    tables is evidence about neither."""
    assert not LEFT_ALONE.asserted_in(R1_RENAME_RESTATES_THE_MISCONCEPTION)


def test_readers_being_repointed_is_not_readers_going_on_reading():
    """R1's third half. "Everything that was reading events is repointed"
    carries a reading word and says the opposite of the claim; the claim is
    that the reading *persists*, which is the second group's job."""
    assert not STILL_READ.asserted_in(R1_RENAME_RESTATES_THE_MISCONCEPTION)


def test_a_denial_in_a_sibling_clause_is_not_evidence_for_the_claim():
    """R2. The words "by hand" survive the denial intact; the clause carrying
    them does not survive the negation check."""
    assert not NARROWING_IS_MANUAL.asserted_in(R2_FILTER_CLAIMS_THE_CONVERSION_ALREADY_DID_IT)


def test_a_contrastive_reversal_with_no_negation_is_not_evidence_for_the_claim():
    """R4. Nothing here is negated -- "only" and "rather than" do the
    reversing -- so no negation check reaches it. The contrast is a clause
    boundary, which puts "every run" and "the whole of this model's source" on
    opposite sides of it."""
    assert not RESCAN.asserted_in(R4_FILTER_CLAIMS_DBT_NARROWS_IT)


def test_an_illustration_in_brackets_is_not_the_claim_the_sentence_dropped():
    """R5. The bracketed example says the same thing for one case; the sentence
    it illustrates no longer says it for any. A parenthetical is a clause of
    its own, so its words cannot stand in for the claim's."""
    assert not NEVER_SELECTED.asserted_in(R5_FILTER_KEEPS_ONLY_THE_ILLUSTRATION)
    # Not vacuous: the same sentence with its claim intact is accepted.
    assert NEVER_SELECTED.asserted_in(
        R5_FILTER_KEEPS_ONLY_THE_ILLUSTRATION.replace(
            "is left where it is", "is selected by no run at all"
        )
    )


# --- positive controls: a matcher that rejects everything would pass all of
# the above. Each claim is shown accepting a text that does assert it.


ACCEPTED = {
    LEFT_ALONE: "events is left exactly as it was, holding what it held before.",
    STILL_READ: "A saved query pointed at events goes on reading events.",
    REPOINTING_IS_MANUAL: "Pointing each of those at stg_events is a change someone has to "
    "make by hand.",
    RESCAN: "So each run goes back over everything that matches it.",
    NEVER_SELECTED: "A row that does not match is taken by no run at all.",
    NARROWING_IS_MANUAL: "Changing it is something someone has to do by hand.",
}


def test_every_claim_accepts_a_text_that_makes_it():
    for claim, text in ACCEPTED.items():
        assert claim.asserted_in(text), f"{claim.name}: {text}"


def test_every_claim_needs_more_than_one_of_its_words():
    """A claim built from two or three groups must fail when only one group is
    present -- otherwise the extra groups are decoration and the conjunction
    is not doing the work the docstrings say it does."""
    for claim in (STILL_READ, RESCAN, NEVER_SELECTED):
        assert len(claim.groups) > 1, claim.name
        for dropped in range(len(claim.groups)):
            partial = Claim(
                name=claim.name,
                groups=tuple(g for i, g in enumerate(claim.groups) if i != dropped),
                subject=claim.subject,
                conflicting=claim.conflicting,
                affirmative=claim.affirmative,
            )
            assert partial.asserted_in(ACCEPTED[claim]), claim.name


# --- the splitter itself


def test_a_contrast_separates_the_halves_it_contrasts():
    """The property R4 turns on, asserted directly rather than only through
    R4: the two sides of "rather than" never share a clause."""
    split = clauses("every run reads the whole source rather than what arrived since then")
    assert len(split) == 2, split
    assert "whole" in split[0] and "whole" not in split[1], split


def test_a_parenthetical_is_its_own_clause():
    """The property R5 turns on. Without it an aside can supply words the
    sentence around it has stopped supplying."""
    split = clauses("a row that does not match is left alone (a late row never enters it)")
    assert len(split) == 2, split
    assert "never" not in split[0], split


def test_a_coordinated_clause_is_its_own_clause():
    split = clauses("it goes on reading events, and never sees a row only stg_events has")
    assert len(split) == 2, split
    assert "never" not in split[0], split
