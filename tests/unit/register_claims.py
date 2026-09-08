"""Deciding whether a piece of engine prose actually makes a claim.

Three rounds of blind review on this branch found the same defect, and each
fix addressed the shape of the instance rather than the shape of the mistake:

| round | what survived | what the fix did |
| --- | --- | --- |
| Task 3, F1 | a stem list with no `hold`/`keep` | added two stems |
| Task 3, F2 | "after" present, the ordering reversed | added a positive assertion |
| Task 4, M9 | a negation *between* subject and claim word | tempered the span between them |

Each widened the window by one notch, and each was verified against the
instance that produced it. None asked the question the defect is actually
about: **does this sentence assert the claim, or deny it?** So the fourth
instance arrived at once -- a negation placed *before* the subject rather than
between (R1), a denial in a sibling clause (R2), and a contrastive reversal
carrying no negation at all (R4) -- all three passing every guard the
round-3 fix produced.

This module therefore does not match spans. It splits prose into CLAUSES and
asks three things of the clause a claim's words sit in:

1. it contains every group of words the claim requires;
2. it names the claim's subject, and names no conflicting subject;
3. for an affirmative claim, it contains no negation at all.

(3) closes polarity in general rather than one position at a time: a negation
*anywhere* in the clause disqualifies that clause as evidence, wherever it
falls relative to the claim word. (2) closes the other half of R1 -- "events
stays exactly where it was, now under the name stg_events" is not evidence
about `events`, because a clause naming both tables cannot be read as a claim
about one of them.

**What this still cannot do**, stated rather than papered over: it cannot
catch a clause that is grammatically affirmative, names only its own subject,
and is simply false. No check of this kind can, and none in this repo claims
to. What it does catch is every way of *denying* a claim while keeping its
words, which is what four adversarial reviews have actually produced.

The cost is brittleness: a rewording that drops a claim word into a clause
carrying an unrelated negation fails a text that is fine. That is the correct
direction. A false failure is seen and fixed; a false pass ships.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Where one clause ends and the next begins. Ordered longest-first where two
# could match at the same place, so ", and" is consumed as the coordinated
# form rather than leaving a dangling comma.
#
# A parenthetical is a clause too, and not a decorative one: the filter
# caveat closes with an illustration in brackets ("a row turning up afterwards
# carrying an earlier one never enters stg_events") that carries every word of
# the claim the sentence before it makes. Without the bracket boundary, gutting
# that claim and leaving the illustration standing satisfied the check --
# mutation P6 in the task report, found by mutating the fix for R4.
#
# The contrastive markers are here for the same reason the coordinators are,
# and they are what catches R4: "over the whole of the source rather than over
# what arrived since the last run" and "over only what arrived since the last
# run rather than over the whole of the source" contain the identical words
# and assert opposite things. Splitting there puts each half of the contrast
# in its own clause, so a conjunction claim can only be satisfied by the half
# that actually asserts it.
CLAUSE_BREAK = re.compile(
    r"(?<=[.;:])\s+"
    r"|\s+(?:--|—)\s+"
    r"|,\s+(?:and|but|so|or|yet)\s+"
    r"|\s+and\s+"
    r"|\s+(?:rather than|instead of|as opposed to|whereas)\s+"
    r"|\s*[()]\s*"
)

NEGATION = re.compile(r"\b(?:not|never|no|none|nothing|nobody|neither|nor)\b|n't")


def clauses(text: str) -> list[str]:
    """`text` split into clauses, lowercased.

    Lowercased here rather than at each comparison so that a claim's word list
    is written once, in one case, and cannot drift from how the check applies
    it.
    """
    return [part.strip() for part in CLAUSE_BREAK.split(text.lower()) if part.strip()]


def names(clause: str, table: str) -> bool:
    """Whether `clause` names `table` as a whole word.

    Whole word and not substring: `"events" in "stg_events"` is true, and a
    claim about the original table satisfied by a mention of the renamed one
    is the wrong-comparison this branch has already shipped once. `_` is a
    word character, so `\\bevents\\b` cannot begin inside `stg_events`.
    """
    return re.search(rf"\b{re.escape(table.lower())}\b", clause.lower()) is not None


@dataclass(frozen=True, slots=True)
class Claim:
    """One assertion a register has to make, and what counts as making it.

    `groups` is a conjunction of disjunctions: every group must be satisfied
    by some word, all within one clause. A single group is a plain vocabulary;
    two or more pin a relation that no single word carries ("every run" is
    unavoidable in prose about runs, and says nothing on its own).

    `subject` is the table the claim is about, required in the same clause.
    `conflicting` are tables whose presence in that clause disqualifies it:
    the claim is about one of them, and a clause naming both is evidence about
    neither.

    `affirmative` is False only where the claim IS a denial ("a row that does
    not match is never selected"), in which case its own groups carry the
    negation and a negation in the clause is expected rather than
    disqualifying.
    """

    name: str
    groups: tuple[tuple[str, ...], ...]
    subject: str = ""
    conflicting: tuple[str, ...] = ()
    affirmative: bool = True

    def clauses_asserting(self, text: str) -> list[str]:
        found: list[str] = []
        for clause in clauses(text):
            if not all(any(word in clause for word in group) for group in self.groups):
                continue
            if self.subject and not names(clause, self.subject):
                continue
            if any(names(clause, other) for other in self.conflicting):
                continue
            if self.affirmative and NEGATION.search(clause):
                continue
            found.append(clause)
        return found

    def asserted_in(self, text: str) -> bool:
        return bool(self.clauses_asserting(text))


# --- the rename's cutover, in whichever register
#
# `events` and `stg_events` are the fixture's two tables; every rename test in
# this repo converts that pair, and naming them here rather than parameterising
# keeps the claims readable at the point they are asserted.

LEFT_ALONE = Claim(
    name="the original table is left exactly as it was",
    groups=(("leaves", "left", "stays", "remains", "unchanged", "untouched", "as it is"),),
    subject="events",
    conflicting=("stg_events",),
)

STILL_READ = Claim(
    name="its readers go on reading it",
    # Two groups: naming the readers is not the claim, and neither is saying
    # something persists. The claim is that the reading persists, and R1's
    # "everything that was reading events is repointed as part of this"
    # satisfies the first group alone.
    groups=(
        ("reading", "reads", "pointed at", "points at", "selecting from", "selects from"),
        ("still", "goes on", "go on", "keeps", "keep", "carries on"),
    ),
    subject="events",
    conflicting=("stg_events",),
)

REPOINTING_IS_MANUAL = Claim(
    name="repointing those readers is the reader's own work",
    groups=(("by hand", "yourself", "someone has to", "manually", "manual"),),
    subject="stg_events",
    conflicting=("events",),
)

# --- the incremental filter's caveat, in whichever register

RESCAN = Claim(
    name="every run re-reads the whole source",
    groups=(("every run", "each run", "run after that"), ("everything", "whole", "all of")),
)

NEVER_SELECTED = Claim(
    name="a row that does not match is selected by no run",
    # Three groups, and the third is what stops "a row that does not match is
    # never left out" -- a denial of the wrong thing, carrying both of the
    # first two groups' words.
    groups=(
        ("never", "no run at all", "by no run", "not by any"),
        ("match", "satisfy"),
        ("selected", "taken", "picked up", "enters", "gets in"),
    ),
    affirmative=False,
)

NARROWING_IS_MANUAL = Claim(
    name="narrowing the filtering is the reader's own work",
    groups=(("by hand", "yourself", "someone has to", "manually", "manual"),),
)
