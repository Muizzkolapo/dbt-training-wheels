"""Deciding whether a piece of engine prose actually makes a claim.

**What this decides, and what it does not.** It decides *syntactic* polarity
and *syntactic* scope: whether the words of a claim appear together, about the
claim's own subject, in a clause that no negation or scope-restricting marker
in its sentence governs, in a sentence that is not a question. It does not
decide meaning, and the following all assert their claim as far as this module
can tell -- verified, not assumed, by
test_a_claim_embedded_under_a_framing_verb_is_a_documented_blind_spot:

    It is a myth that events is left exactly as it was.      (lexical denial)
    People assume events is left exactly as it was.          (attribution)
    The following is false. events is left exactly as it was. (cross-sentence)
    events would be left exactly as it was.                  (modal)
    events is left exactly as it was, under its new name.    (affirmative and false)

Each of those denies or suspends the claim with no negation, no scope word and
no clause boundary to see. Closing them means deciding what a sentence MEANS,
which a word-and-punctuation checker does not do; every attempt so far has
been another vocabulary, and the next paraphrase has defeated each one. So
they are documented rather than chased, and the engine prose in this class is
reviewed by a person.

This limit is stated here because the two versions before this one claimed
rule 3 "closes polarity in general" and catches "every way of denying a claim
while keeping its words". Both were false as shipped.

**What it costs.** Strictness is paid for in true sentences it rejects, and
these are real: "Pointing each of those at stg_events, away from events, is
manual work" (names both tables), "Nothing writes to it, so events is left
exactly as it was" (a negation earlier in the sentence), "Each run reads the
whole of that table, including what arrived since the last run" (names the
slice). A rejection is seen at the moment someone writes it; an acceptance of
a reversal ships. That is why the cost is paid in this direction.

**Why it is shaped this way.** Five rounds of adversarial review found the same
defect, and the first four fixes each answered the counter-example rather than
the property behind it:

| round | what survived | what the fix did |
| --- | --- | --- |
| Task 3, F1 | a stem list with no `hold`/`keep` | added two stems |
| Task 3, F2 | "after" present, the ordering reversed | added a positive assertion |
| Task 4, M9 | a negation between subject and claim word | tempered the span between |
| Task 4 fix 1, R1/R2/R4 | a negation before the subject; a denial in a
  sibling clause; a contrastive reversal | split into clauses, checked each |
| Task 4 fix 2, V2/V3/V4 | claims with no subject; a negation coordinated
  across a comma; `reads` matched inside `spreadsheet` | this version |

The fourth of those left `\\s+and\\s+` and `rather than` in the splitter
because R1 and R4 happened to use them, which is how "dbt does not create
stg_events beside it **and** leave events exactly as it is" came through: a
comma is not where a negation stops applying. So polarity is no longer scoped
to a clause. It is scoped **forward to the end of the sentence**: a marker in
clause *n* governs clauses *n* onward, and stops at the full stop. That is
where English actually puts it, it kills the whole coordinated-negation family
at once, and it accepts the clearest true phrasings ("Every run re-reads all of
the source, not just the new rows") that clause scope rejected.

Three rules, then, and a claim is asserted only where all three hold in one
clause:

1. every group of words the claim requires is present, matched on **word
   boundaries** -- `reads` must not be found inside `spreadsheet`, which is how
   a sentence with no reading verb in it asserted that the readers still read;
2. the clause names the claim's subject and no conflicting term -- a claim
   about one table made in a clause naming two is evidence about neither, and
   a claim about the whole source made in a clause naming the incremental
   slice is evidence about neither;
3. no polarity marker earlier in the same sentence governs it.

The cost is brittleness: a rewording that drops a claim word into a sentence
already carrying a negation fails a text that is fine. That direction is
deliberate. A false failure is seen and fixed; a false pass ships.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A sentence ends at a stop. Polarity does not cross one.
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")

# Inside a sentence, where one predication ends and the next begins -- but only
# at markers that are unambiguously clause-level. `;` `:` a dash pair and a
# bracketed aside are punctuation that can only separate clauses. A contrastive
# subordinator introduces the alternative being REJECTED, so the two sides of
# it assert opposite things and a conjunction claim must not be allowed to
# straddle it.
#
# A bare `and` is NOT here, and neither is `, so`. They were, because two
# review counter-examples used them; they joined phrases as often as clauses,
# and the engine prose was punctuated to suit them -- which is backwards.
# Sentence-scoped polarity covers what they were doing, without asking a writer
# to place a comma for a test.
CLAUSE_BREAK = re.compile(
    r"(?<=[;:])\s+"
    r"|\s+(?:--|—)\s+"
    r"|\s+(?:rather than|instead of|as opposed to|whereas)\s+"
    r"|\s*[()]\s*"
    # A comma directly before a negative appositive. "A, not B" asserts A and
    # denies B -- the negation belongs to what follows it, which is the one
    # place a comma really is where a negation starts. Without this the
    # clearest true phrasing of the rescan claim ("Every run re-reads all of
    # the source, not just the new rows") was rejected while a coherent
    # reversal was accepted, and strictness that costs the true sentence and
    # not the false one is buying nothing.
    r"|,\s+(?=(?:not|never|rather)\b)"
)

# Negation, plus the markers that restrict a clause's scope rather than deny it
# outright: "events is left as it was, except where a job writes to it" is not
# the unconditional claim the text has to make.
# `rather than` is deliberately absent: it is a clause break above, so it
# never reaches a clause, and listing it here as well would make removing the
# break silently reverse this module's verdict on the sentence it splits.
NEGATION = re.compile(
    r"\b(?:not|never|no|none|nothing|nobody|neither|nor|without|"
    r"except|unless|apart from|other than)\b|n't"
)


def sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_BREAK.split(text) if part.strip()]


def clauses(text: str) -> list[str]:
    """Every clause of `text`, lowercased, sentence boundaries included."""
    return [clause for clause, _ in scoped_clauses(text)]


def scoped_clauses(text: str) -> list[tuple[str, bool]]:
    """Every clause of `text`, each paired with whether a polarity marker in
    its own sentence governs it.

    Forward-scoped and sentence-bounded: the marker governs the clause it sits
    in and every later clause up to the full stop, and governs nothing after
    it. "Everything selecting from events goes on reading events. It never
    sees a row only stg_events has." keeps its claim; "dbt does not create
    stg_events and leave events exactly as it is." does not.
    """
    out: list[tuple[str, bool]] = []
    for sentence in sentences(text.lower()):
        # A question asserts nothing. This is the only one of the non-syntactic
        # reversals below that can be decided without semantics, so it is the
        # only one closed: "Is events left exactly as it was? No." otherwise
        # counts as evidence that it is.
        if sentence.endswith("?"):
            continue
        governed = False
        for clause in CLAUSE_BREAK.split(sentence):
            clause = clause.strip()
            if not clause:
                continue
            if NEGATION.search(clause):
                governed = True
            out.append((clause, governed))
    return out


def names(clause: str, term: str) -> bool:
    """Whether `clause` uses `term` as a whole word.

    Whole word and not substring, for both the subjects and the claim words
    below. `"events" in "stg_events"` is true and `"reads" in "spreadsheet"`
    is true, and each of those has already been a text that asserted the
    opposite of the claim it satisfied. `_` is a word character, so
    `\\bevents\\b` cannot begin inside `stg_events`.
    """
    return re.search(rf"\b{re.escape(term.lower())}\b", clause.lower()) is not None


@dataclass(frozen=True, slots=True)
class Claim:
    """One assertion a register has to make, and what counts as making it.

    `groups` is a conjunction of disjunctions: every group must be satisfied
    within one clause. One group is a bare vocabulary and says only that a
    word occurred; the relation lives in having more than one. "every run" is
    unavoidable in prose about runs and asserts nothing by itself.

    `subject` is what the claim is about, as any of the spellings the two
    registers use for it -- the dbt one says `source`, the plain one says
    `table`, and they are the same subject. Required in the same clause.

    `conflicting` are terms whose presence in that clause makes it evidence
    about something else: the other table, or the incremental slice rather
    than the whole source.

    `affirmative` is False only where the claim IS a denial ("a row that does
    not match is selected by no run"), in which case its own groups carry the
    negation and a governing marker is expected rather than disqualifying.
    """

    name: str
    groups: tuple[tuple[str, ...], ...]
    subject: tuple[str, ...] = ()
    conflicting: tuple[str, ...] = ()
    affirmative: bool = True

    def clauses_asserting(self, text: str) -> list[str]:
        found: list[str] = []
        for clause, governed in scoped_clauses(text):
            if not all(any(names(clause, word) for word in group) for group in self.groups):
                continue
            if self.subject and not any(names(clause, term) for term in self.subject):
                continue
            if any(names(clause, other) for other in self.conflicting):
                continue
            if self.affirmative and governed:
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
    subject=("events",),
    conflicting=("stg_events",),
)

STILL_READ = Claim(
    name="its readers go on reading it",
    groups=(
        # what the readers do. `reads` and `reading` are separate entries
        # because matching is now on word boundaries, so neither contains the
        # other any more -- and `reads` inside `spreadsheet` was a live defect.
        ("reading", "reads", "read", "pointed", "points", "selecting", "selects"),
        # and that they go on doing it. R1's "everything that was reading
        # events is repointed as part of this" satisfies the first group alone.
        ("still", "goes on", "go on", "keeps", "keep", "carries on", "carry on"),
    ),
    subject=("events",),
    conflicting=("stg_events",),
)

REPOINTING_IS_MANUAL = Claim(
    name="repointing those readers at the new table is the reader's own work",
    groups=(
        ("repointing", "repoint", "pointing", "point"),
        ("by hand", "yourself", "someone has to", "manual", "manually"),
    ),
    subject=("stg_events",),
    conflicting=("events",),
)

# --- the incremental filter's caveat, in whichever register

RESCAN = Claim(
    name="every run re-reads the whole of the model's source",
    groups=(
        ("every run", "each run", "run after that"),
        ("everything", "whole", "all of"),
    ),
    # What is re-read, which is the half that was missing: "every run
    # evaluates it over the whole of what arrived since the last run" carries
    # both groups and asserts the opposite.
    subject=("source", "table", "tables", "history"),
    conflicting=("since the last run", "what arrived", "new rows", "only what is new"),
)

NEVER_SELECTED = Claim(
    name="a row that does not match is selected by no run",
    groups=(
        ("never", "no run at all", "by no run", "not by any"),
        ("match", "matches", "satisfy", "satisfies"),
        ("selected", "taken", "picked up", "enters", "gets in"),
    ),
    subject=("row", "rows"),
    affirmative=False,
)

NARROWING_IS_MANUAL = Claim(
    name="narrowing the filtering is the reader's own work",
    groups=(
        ("narrowing", "narrow", "narrowed", "changing", "change"),
        ("by hand", "yourself", "someone has to", "manual", "manually"),
    ),
    subject=("filtering", "filter", "condition"),
    # "This conversion has already narrowed the filtering for you" names the
    # subject and the action and says the reader has nothing to do.
    conflicting=("already", "for you"),
)
