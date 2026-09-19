"""The texts that got past this branch's guards, held as pins.

These are regression pins, not coverage. Each defeated a specific version of
the claim matching in `tests/unit/register_claims.py` and survived the whole
suite at the commit it was written against. Pinning one says "this exact
defeat does not work any more". It does not say the class is closed, and that
module's docstring is explicit about what it can and cannot decide.

Provenance, because it matters which of these were found by attacking the fix
rather than the defect: R1, R2 and R4 came from the first blind review, R3
from the same review (pinned in test_rename_cutover.py beside the assertion it
defeated), R5 from mutating the fix for R4, and V2, V3, V4 and V5 from the
second blind review. V5 is the odd one -- it is a true sentence the guards
REJECTED, which is a defect of the same kind: a checker that rejects the
clearest true phrasing while accepting a coherent reversal is not buying
anything with its strictness.

R1 is quoted from the review with its elisions filled by the shortest
connective text that makes it a whole paragraph -- every fragment the review
quoted appears verbatim, and the filled spans carry no claim words. R2, R4 and
the V texts are verbatim, with the reviews' bold markers removed and R4's
`{condition}` substituted with the fixture's own condition.
"""

from __future__ import annotations

import pytest
from tests.unit.register_claims import (
    LEFT_ALONE,
    NARROWING_IS_MANUAL,
    NEVER_SELECTED,
    REPOINTING_IS_MANUAL,
    RESCAN,
    STILL_READ,
    Claim,
    clauses,
    scoped_clauses,
)

# R1 -- the rename's plain register, with the Django `RenameModel`
# misconception restated by the tool as fact. It survived commit c235102
# because the matcher inspected only the span BETWEEN the table name and the
# claim word, so "do not" sitting before `stg_events` was invisible.
R1_RENAME_RESTATES_THE_MISCONCEPTION = (
    "Renaming moves the table for you. Everything that was reading events is repointed as "
    "part of this, and then it all points at stg_events instead — events stays exactly "
    "where it was, now under the name stg_events. You do not have to point anything at "
    "stg_events yourself, and there is nothing further to do."
)

# R2 -- the filter caveat's closing sentence. It survived because the filter
# test file ran its manual-work words through a bare substring search while
# the rename file ran the same words through the tempered helper.
R2_FILTER_CLAIMS_THE_CONVERSION_ALREADY_DID_IT = (
    "Narrowing the filter, or guarding it, is not a change anyone has to make by hand -- "
    "this conversion has already done it."
)

# R4 -- the filter caveat's rescan sentence, reversed. No negation anywhere:
# the contrast carries the reversal, and both halves of the conjunction are
# present.
R4_FILTER_CLAIMS_DBT_NARROWS_IT = (
    "So dbt narrows it for you — every run evaluates occurred_at > '2024-01-01' over only "
    "what arrived since the last run rather than over the whole of this model's source, so "
    "the work re-done stays small."
)

# R5 -- found by mutating the fix for R4. The caveat closes with an
# illustration in brackets carrying every word of the claim the sentence
# before it makes, so gutting that claim and leaving the illustration standing
# still satisfied the check.
R5_FILTER_KEEPS_ONLY_THE_ILLUSTRATION = (
    "and a row that does not match is left where it is, however late it arrives (if the "
    "filtering compares a date, a row turning up afterwards carrying an earlier one never "
    "enters stg_events)"
)

# V2 -- two claims that carried no subject and no relation, so a sentence
# asserting the opposite carried all of their words. The second is worse than
# the first: its `name` field mentioned narrowing and filtering, neither of
# which it checked.
V2_RESCAN_OVER_THE_NEW_ROWS_ONLY = (
    "It stays a fixed condition: every run evaluates it over the whole of what arrived "
    "since the last run."
)
V2_NARROWING_ALREADY_DONE_FOR_YOU = (
    "This conversion has already narrowed the filtering for you; reviewing the model file "
    "is manual work it leaves to you."
)

# V3 -- one negation, two verb phrases. Clause-scoped polarity stopped at the
# comma or the colon; English does not.
V3_COORDINATED_NEGATIONS = (
    "dbt does not create stg_events beside it and leave events exactly as it is.",
    "This conversion never builds a second table and leaves events standing untouched.",
    "This does not happen: events is left exactly as it was.",
    "Do not assume the following: events stays exactly as it is.",
)

# V4 -- `reads` matched inside `spreadsheet`. The plain register lists concrete
# readers, so adding one more to the list was the most likely next edit.
V4_A_READER_WITH_NO_READING_VERB = (
    "So a dashboard or a spreadsheet built on events is still switched over for you."
)

# V5 -- a true sentence the guards rejected. Held for the opposite reason to
# every other pin here.
V5_THE_CLEAREST_TRUE_PHRASING = "Every run re-reads all of the source, not just the new rows."


# --- the defeats, each rejected by the claim it was written against


def test_a_negation_before_the_subject_is_not_evidence_for_the_claim():
    """R1's manual-work sentence puts "do not" ahead of the table name."""
    assert not REPOINTING_IS_MANUAL.asserted_in(R1_RENAME_RESTATES_THE_MISCONCEPTION)


def test_a_claim_about_one_table_made_in_a_clause_naming_both_is_not_evidence():
    """R1's other half: "events stays exactly where it was, now under the name
    stg_events" is affirmative and carries the claim word, and describes a
    rename rather than a table left alone."""
    assert not LEFT_ALONE.asserted_in(R1_RENAME_RESTATES_THE_MISCONCEPTION)


def test_readers_being_repointed_is_not_readers_going_on_reading():
    """R1's third half: the claim is that the reading PERSISTS."""
    assert not STILL_READ.asserted_in(R1_RENAME_RESTATES_THE_MISCONCEPTION)


def test_a_denial_in_a_sibling_clause_is_not_evidence_for_the_claim():
    """R2. The words survive the denial; the clause does not."""
    assert not NARROWING_IS_MANUAL.asserted_in(R2_FILTER_CLAIMS_THE_CONVERSION_ALREADY_DID_IT)


def test_a_contrastive_reversal_with_no_negation_is_not_evidence_for_the_claim():
    """R4. Nothing is negated -- "only" and "rather than" do the reversing."""
    assert not RESCAN.asserted_in(R4_FILTER_CLAIMS_DBT_NARROWS_IT)


def test_an_illustration_in_brackets_is_not_the_claim_the_sentence_dropped():
    """R5. A bracketed example cannot stand in for the claim it illustrates."""
    assert not NEVER_SELECTED.asserted_in(R5_FILTER_KEEPS_ONLY_THE_ILLUSTRATION)
    # Not vacuous: the same sentence with its claim intact is accepted.
    assert NEVER_SELECTED.asserted_in(
        R5_FILTER_KEEPS_ONLY_THE_ILLUSTRATION.replace(
            "is left where it is", "is selected by no run at all"
        )
    )


def test_a_claim_with_no_subject_is_satisfied_by_a_sentence_about_something_else():
    """V2. "the whole of what arrived since the last run" carries both of
    RESCAN's groups and names the incremental slice, which is the thing the
    claim exists to deny. "already narrowed the filtering for you" carries
    NARROWING's action and its subject and says the reader has nothing to do."""
    assert not RESCAN.asserted_in(V2_RESCAN_OVER_THE_NEW_ROWS_ONLY)
    assert not NARROWING_IS_MANUAL.asserted_in(V2_NARROWING_ALREADY_DONE_FOR_YOU)
    # The subject on its own, with nothing else to reject it by: the sentence
    # above is also caught by `conflicting`, so without these two the subject
    # could be deleted from either claim and only a meta-test would notice.
    assert not RESCAN.asserted_in("Every run evaluates it over the whole of that slice.")
    assert not NARROWING_IS_MANUAL.asserted_in("Narrowing it is something someone does by hand.")


@pytest.mark.parametrize("text", V3_COORDINATED_NEGATIONS, ids=lambda t: t[:32])
def test_one_negation_governs_every_verb_phrase_after_it_in_its_sentence(text: str):
    """V3. A comma and a colon are where clause-scoped polarity stopped and
    where English does not: all four of these deny exactly the claim their
    second half appears to make."""
    assert not LEFT_ALONE.asserted_in(text)


def test_a_claim_word_found_inside_a_longer_word_is_not_evidence():
    """V4. `reads` is inside `spreadsheet`, so a sentence with no reading verb
    in it asserted that the readers go on reading."""
    assert not STILL_READ.asserted_in(V4_A_READER_WITH_NO_READING_VERB)


def test_a_trailing_negation_does_not_disqualify_the_claim_before_it():
    """V5, and the direction the other pins do not cover. Polarity scopes
    FORWARD: a negation that narrows what follows it says nothing about the
    clause in front of it, and the clearest true phrasing of this claim puts
    one there. A checker that rejects this while accepting R4 is not buying
    anything with its strictness."""
    assert RESCAN.asserted_in(V5_THE_CLEAREST_TRUE_PHRASING)


# The reversals that survive, pinned as the documented blind spot rather than
# left for a sixth review to rediscover as a finding. Each denies or suspends
# its claim with no negation, no scope word and no clause boundary to see.
FRAMED_REVERSALS = (
    ("lexical denial", LEFT_ALONE, "It is a myth that events is left exactly as it was."),
    ("attribution", LEFT_ALONE, "People assume events is left exactly as it was."),
    (
        "cross-sentence denial",
        LEFT_ALONE,
        "The following is false. events is left exactly as it was.",
    ),
    ("modal", LEFT_ALONE, "events would be left exactly as it was."),
    ("affirmative and false", LEFT_ALONE, "events is left exactly as it was, under its new name."),
    (
        "hedge",
        STILL_READ,
        "A saved query pointed at events probably goes on reading events.",
    ),
)


def test_the_docstring_names_exactly_the_blind_spots_pinned_here():
    """Nothing bound the module docstring's list to `FRAMED_REVERSALS`
    before this test existed: the docstring could name five cases while six
    were pinned, and both the suite and the docstring's own claim to be
    "verified, not assumed" would stay green. The label in parentheses at
    the end of each docstring line is read back and compared against this
    file's own labels, in order, so the two cannot drift apart silently
    again.
    """
    import re

    import tests.unit.register_claims as module

    labelled = re.findall(r"\(([a-z][a-z -]*)\)\s*$", module.__doc__ or "", re.MULTILINE)
    assert labelled == [label for label, _claim, _text in FRAMED_REVERSALS]


@pytest.mark.parametrize(
    ("label", "claim", "text"), FRAMED_REVERSALS, ids=[case[0] for case in FRAMED_REVERSALS]
)
def test_a_claim_embedded_under_a_framing_verb_is_a_documented_blind_spot(
    label: str, claim: Claim, text: str
):
    """These assert their claim, and should not.

    This is not a guard and it is not an accepted bug. It is the boundary of
    what a word-and-punctuation checker decides, written down so that the
    module's docstring can be checked against something rather than believed,
    and so that the next review finds it already named instead of reporting it
    as new. Five rounds of this branch have been spent widening vocabularies
    at this boundary and every widening was defeated by the next paraphrase.

    If a later change genuinely closes one of these, this test fails and gets
    deleted -- deliberately, with the docstring's list shortened to match.
    """
    assert claim.asserted_in(text), f"{label} now rejected; shorten the documented limit"


def test_a_question_asserts_nothing():
    """The one non-syntactic reversal that can be decided without semantics,
    and so the only one closed: a sentence ending in a question mark makes no
    claim, whatever words it contains."""
    assert not LEFT_ALONE.asserted_in("Is events left exactly as it was? No.")
    # Not vacuous: the same words as a statement are accepted.
    assert LEFT_ALONE.asserted_in("events is left exactly as it was.")


# --- positive controls: a matcher that rejects everything passes all of the
# above. Each claim is shown accepting a text that does assert it.

ACCEPTED = {
    LEFT_ALONE: "events is left exactly as it was.",
    STILL_READ: "A saved query pointed at events goes on reading events.",
    REPOINTING_IS_MANUAL: "Pointing each of those at stg_events is a change someone has to "
    "make by hand.",
    RESCAN: "Each run goes back over the whole of that table again.",
    NEVER_SELECTED: "A row that does not match is taken by no run at all.",
    NARROWING_IS_MANUAL: "Narrowing the filtering is something someone has to do by hand.",
}


def test_every_claim_accepts_a_text_that_makes_it():
    for claim, text in ACCEPTED.items():
        assert claim.asserted_in(text), f"{claim.name}: {text}"


# One text per group of every multi-group claim: a sentence that satisfies the
# claim's OTHER groups and not this one. Dropping the group must turn a
# rejection into an acceptance -- which is what "this group is load-bearing"
# means, and what the previous version of the test below did not check.
WITNESSES: dict[Claim, dict[int, str]] = {
    STILL_READ: {
        0: "events still holds what it held.",
        1: "A saved query pointed at events.",
    },
    REPOINTING_IS_MANUAL: {
        0: "stg_events is something someone has to do by hand.",
        1: "Pointing each of those at stg_events.",
    },
    RESCAN: {
        0: "It is re-read over the whole of the source.",
        1: "Every run re-reads the source.",
    },
    NEVER_SELECTED: {
        0: "A row whose value does not match is selected.",
        1: "A row is selected by no run at all.",
        2: "A row that does not match is never there.",
    },
    NARROWING_IS_MANUAL: {
        0: "The filtering is something someone has to do by hand.",
        1: "Narrowing the filtering.",
    },
}


def test_every_group_of_a_multi_group_claim_is_load_bearing():
    """Each group must be the difference between rejection and acceptance.

    The version of this test shipped in fix round 1 asserted that the WEAKENED
    claim still accepts a text the FULL claim already accepts. Dropping a
    conjunct from a conjunction is monotone -- the accepting set can only grow
    -- so that assertion could never fail for any claim, any group, any text:
    the ninth tautological guard on this branch, and the one guarding against
    decoration. The direction that carries the meaning is this one.
    """
    # Iterated from the authored table, not from the code: deriving the list
    # from `len(claim.groups) > 1` means a claim that LOSES a group quietly
    # drops out of the loop, and dropping STILL_READ's second group left this
    # whole suite green. The two assertions below are what make that fail --
    # the first because the claim is no longer multi-group, the second because
    # its witness indices no longer match its groups.
    assert set(WITNESSES) == {claim for claim in ACCEPTED if len(claim.groups) > 1}
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


def test_every_claim_names_a_subject():
    """A claim with no subject is a word search. V2 is what that costs."""
    for claim in ACCEPTED:
        assert claim.subject, claim.name


# --- the splitter and the scope rule


def test_a_contrast_separates_the_halves_it_contrasts():
    """The two sides of "rather than" assert opposite things and never share a
    clause."""
    split = clauses("every run reads the whole source rather than what arrived since then")
    assert len(split) == 2, split
    assert "whole" in split[0] and "whole" not in split[1], split


def test_a_parenthetical_is_its_own_clause():
    split = clauses("a row that does not match is left alone (a late row never enters it)")
    assert len(split) == 2, split
    assert "never" not in split[0], split


def test_a_negation_governs_the_rest_of_its_sentence_and_stops_at_the_stop():
    """The scope rule itself, in both directions. A comma is not where a
    negation stops applying, and a full stop is."""
    scoped = scoped_clauses(
        "dbt does not create it; it leaves events alone. Everything still reads events."
    )
    governed = [governed for _, governed in scoped]
    assert governed == [True, True, False], scoped
