"""What the rename leaves behind, in both registers.

Renaming `events` to `stg_events` is not a migration. dbt writes the relation
its own model names and never touches the other one, so the original stands
exactly as it was with every reader still pointed at it. A backend engineer in
the persona walkthroughs read the rename as a Django `RenameModel` --
"atomic, data-preserving, references updated for me" -- and called the
correction, when a prototype gave it, the single best piece of
expectation-management on the page, because that instinct "would have caused a
real incident if unaddressed". Two of five personas asked for it independently
(spec section 11.6).

The escape hatch -- naming the model back to the original so dbt adopts the
existing table -- is deliberately not described here. Section 11.6 records it
as the scariest sentence on the page for both engineers, and it needs
mechanics this slice cannot supply.

On the matching in this file: every assertion about the ORIGINAL table matches
`events` as a whole word. `"events" in text` is satisfied by `"stg_events"`
alone, so a sentence that names only the new table would pass a substring
check while telling the reader nothing about the one left behind. That is the
exact wrong-comparison this branch has already shipped once, and
`test_the_original_is_matched_as_its_own_word_not_inside_the_new_name` is the
proof that this file does not repeat it.
"""

from __future__ import annotations

import re

from tests.unit.assemble.helpers import convert
from tests.unit.passes.test_plain_register import JARGON

RENAMED = "CREATE TABLE events AS SELECT 1 AS id;\n"
ALREADY_PREFIXED = "CREATE TABLE stg_events AS SELECT 1 AS id;\n"

# `_` is a word character, so `\bevents` cannot begin inside `stg_events`.
_ORIGINAL = re.compile(r"\bevents\b")

# Positive claims the text has to make, as vocabularies rather than as a list
# of words it may not use. A blacklist over ("moved", "replaces", ...) is
# evaded by the first paraphrase -- "superseded by", "swapped for" -- and this
# branch has twice shipped a blacklist that only ever met the words already
# inside it. These say what the text must assert instead, which a paraphrase
# of the wrong claim cannot satisfy at all. Both paraphrases are in the task
# report's mutation table (M3, M4): the brief's blacklist passes them and
# these fail them.
#
# `read` is deliberately absent from the second list: it occurs inside
# "already", so it would match a sentence that says nothing about readers.
_LEFT_ALONE = ("leaves", "left", "stays", "remains", "unchanged", "untouched", "as it is")
_STILL_READ = ("reading", "reads", "pointed at", "points at", "selecting from", "selects from")
_BY_HAND = ("by hand", "yourself", "someone has to", "manually", "manual")

# A negation sitting between the table's name and the claim word reverses the
# claim while leaving every word a plain search looks for in place: "events is
# not left as it is" satisfies "does a sentence naming events contain
# 'left'?". So a claim counts only where it is reachable from the name without
# passing one of these. This is not decoration -- mutation M9 in the task
# report is exactly that sentence, and it passed every guard in this file
# before the tempering below was added.
_NEGATION = r"(?:\bnot\b|\bnever\b|\bno\b|\bnothing\b|n't)"


def _rename(change):
    (decision,) = [d for d in change.decisions if d.key.startswith("assemble.rename.")]
    return decision


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.;:])\s+", text) if part.strip()]


def _naming_the_original(text: str) -> list[str]:
    """Every sentence of `text` that names the original table as its own word."""
    return [sentence for sentence in _sentences(text) if _ORIGINAL.search(sentence)]


def _affirmed_of(text: str, table: str, vocabulary: tuple[str, ...]) -> list[str]:
    """Every sentence of `text` affirming one of `vocabulary` of `table`: the
    claim word reachable from that table's own name, in either order, without
    a negation in between.
    """
    claim = "|".join(re.escape(word) for word in vocabulary)
    pattern = re.compile(
        rf"\b{table}\b(?:(?!{_NEGATION})[^.;:])*?(?:{claim})"
        rf"|(?:{claim})(?:(?!{_NEGATION})[^.;:])*?\b{table}\b"
    )
    return [sentence for sentence in _sentences(text) if pattern.search(sentence.lower())]


def _affirmed_of_the_original(text: str, vocabulary: tuple[str, ...]) -> list[str]:
    return _affirmed_of(text, "events", vocabulary)


def test_the_original_is_matched_as_its_own_word_not_inside_the_new_name():
    """The matcher every other assertion here depends on, shown to
    discriminate. The third assertion is the point: the substring check this
    replaces cannot tell the two tables apart, so a text that named only the
    new one would have passed it."""
    assert _ORIGINAL.search("events keeps the rows it already has")
    assert not _ORIGINAL.search("dbt creates stg_events and fills it")
    assert "events" in "dbt creates stg_events and fills it"


def test_a_claim_negated_between_the_name_and_the_claim_word_does_not_count():
    """The tempering, shown to discriminate, on the sentence that motivated
    it. The last assertion is the point: the plain word search this replaces
    reads the negated sentence as making the claim."""
    assert _affirmed_of_the_original("events, which this conversion leaves as it is", _LEFT_ALONE)
    assert not _affirmed_of_the_original("events is not left as it is", _LEFT_ALONE)
    assert "left" in "events is not left as it is"


def test_the_plain_cutover_names_the_table_left_behind_as_well_as_the_new_one():
    """Two tables, both named. A reader who is shown only `stg_events` learns
    a new file exists and nothing about the one still standing beside it,
    which is the half of the fact that causes the incident."""
    plain = _rename(convert(RENAMED)).plain_reason
    assert plain, "the rename Decision carries no plain reason"
    assert "stg_events" in plain, plain
    assert _ORIGINAL.search(plain), plain


def test_the_plain_cutover_says_the_original_is_left_as_it_is_and_still_read():
    """The two claims that refute the migration reading, each required of the
    ORIGINAL table rather than of the text as a whole -- a text asserting them
    about `stg_events` would be saying something else entirely."""
    plain = _rename(convert(RENAMED)).plain_reason
    assert _naming_the_original(plain), plain
    assert _affirmed_of_the_original(plain, _LEFT_ALONE), plain
    assert _affirmed_of_the_original(plain, _STILL_READ), plain


def test_the_plain_cutover_says_repointing_the_readers_is_the_reader_s_own_work():
    """The consequence of the two claims above, and the one a reader acts on.
    Without it the text describes a situation and leaves the reader with no
    reason to think it is theirs to resolve.

    Required of the NEW table, because that is what the work is: pointing the
    readers at `stg_events`. "...so nothing has to be changed by hand" carries
    every word this looks for and says the opposite -- it is what mutation M4
    in the task report produces -- and the same negation tempering the two
    claims above use is what separates them."""
    plain = _rename(convert(RENAMED)).plain_reason
    assert _affirmed_of(plain, "stg_events", _BY_HAND), plain


def test_the_plain_cutover_uses_no_dbt_vocabulary():
    """The plain register's standing rule. Asserting the text is non-empty
    first is not ceremony: the sweep below passes trivially on an empty
    string, which is how a missing plain register would look."""
    plain = _rename(convert(RENAMED)).plain_reason
    assert plain, "the rename Decision carries no plain reason"
    found = [term for term in JARGON if term in plain.lower()]
    assert not found, f"the plain cutover uses {found}: {plain}"


def test_the_plain_cutover_does_not_offer_the_adopt_the_existing_table_manoeuvre():
    """Naming the model back to the original, so dbt takes over the table that
    is already there, is the escape hatch section 11.6 records as the scariest
    sentence on the page for two engineers -- it needs mechanics this slice
    does not have.

    This is a blacklist and is worth only what a blacklist is worth: it will
    catch a later edit that adds the manoeuvre in one of these words, and it
    will not catch one that invents a phrasing for it. It is here to make that
    edit trip a test, not to certify the sentence below it.
    """
    plain = _rename(convert(RENAMED)).plain_reason.lower()
    # Every sweep below passes on an empty string, which is what a missing
    # plain register looks like.
    assert plain, "the rename Decision carries no plain reason"
    for hatch in ("name it back", "rename it back", "back to events", "adopt", "alias"):
        assert hatch not in plain, hatch


def test_the_dbt_register_carries_the_cutover_without_losing_the_convention():
    """Both registers state the consequence; neither is the other's copy. The
    dbt-native one also has a second job the plain one does not -- saying why
    the model was renamed at all -- and appending the cutover must not replace
    the prefix evidence that answers it."""
    decision = _rename(convert(RENAMED))
    reason = decision.reason
    assert "stg_events" in reason, reason
    assert _ORIGINAL.search(reason), reason
    assert "stg_" in reason and "prefix" in reason, reason
    assert _affirmed_of_the_original(reason, _LEFT_ALONE), reason
    assert _affirmed_of_the_original(reason, _STILL_READ), reason
    assert reason != decision.plain_reason


def test_a_model_already_carrying_the_prefix_is_not_renamed_and_claims_no_cutover():
    """No rename, no cutover to warn about, and no Decision inventing one. The
    model assertion is what keeps this from passing for the wrong reason: a
    conversion that produced nothing at all would also record no rename."""
    change = convert(ALREADY_PREFIXED)
    assert [model.name for model in change.models] == ["stg_events"]
    assert not [d for d in change.decisions if d.key.startswith("assemble.rename.")]
    assert not [d for d in change.decisions if d.plain_reason]
