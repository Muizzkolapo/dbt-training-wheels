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

Whether a register actually makes a claim is decided by
`tests/unit/register_claims.py`, which is shared with the filter caveat's
tests rather than copied into them: the first version of these two files
defined the same manual-work vocabulary twice and ran it through two different
checks, and the weaker of the two shipped. That module's docstring records why
it works on clauses rather than on spans, and
`tests/unit/test_register_claims.py` holds the texts that defeated the earlier
versions.
"""

from __future__ import annotations

from tests.unit.assemble.helpers import context_for, convert
from tests.unit.passes.test_plain_register import JARGON
from tests.unit.register_claims import (
    LEFT_ALONE,
    REPOINTING_IS_MANUAL,
    STILL_READ,
    names,
)

RENAMED = "CREATE TABLE events AS SELECT 1 AS id;\n"
ALREADY_PREFIXED = "CREATE TABLE stg_events AS SELECT 1 AS id;\n"

# R3 -- a reason that keeps the word "prefix" and drops both the prefix itself
# and the detection evidence behind it. A blind review got it past
# `assert "stg_" in reason and "prefix" in reason`, where the first half is
# entailed by the `"stg_events" in reason` assertion on the line above it and
# so was never testing anything. Held verbatim for the same reason the other
# three defeats are held in tests/unit/test_register_claims.py.
R3_REASON_WITHOUT_THE_EVIDENCE = (
    "the staging layer's models all share a common prefix. The rename is not a migration: "
    "dbt writes the relations its own models name, and events is not one of them, so dbt "
    "creates stg_events beside it. events is left exactly as it was, and nothing in this "
    "project writes to it again. Everything still selecting from events goes on reading "
    "events, and never sees a row only stg_events has. Repointing those readers at "
    "stg_events is manual work this conversion leaves to you."
)


def _rename(change):
    (decision,) = [d for d in change.decisions if d.key.startswith("assemble.rename.")]
    return decision


def _prefix_convention():
    """The prefix and the evidence for it, read off the real project rather
    than spelled here. Authoring them would make this test agree with itself
    instead of with what the assembler was handed."""
    (detection,) = [d for d in context_for().detections if d.key == "layer.staging.prefix"]
    assert detection.value and detection.evidence, detection
    return detection.value, detection.evidence


def _keeps_the_convention(reason: str, prefix: str, evidence: str) -> bool:
    """Whether `reason` still answers "why was it renamed at all".

    The prefix as the reason spells it (`repr`, since the sentence quotes it)
    and the detection's own evidence string, each as its own value. Not
    `"stg_" in reason`: that is entailed by the model's new name appearing
    anywhere in the sentence, which is why R3 passed it.
    """
    return repr(prefix) in reason and evidence in reason


def test_the_original_is_matched_as_its_own_word_not_inside_the_new_name():
    """The matcher every assertion about the original depends on, shown to
    discriminate. The third assertion is the point: the substring check this
    replaces cannot tell the two tables apart, so a text naming only the new
    one would have passed it."""
    assert names("events keeps the rows it already has", "events")
    assert not names("dbt creates stg_events and fills it", "events")
    assert "events" in "dbt creates stg_events and fills it"


def test_the_plain_cutover_names_the_table_left_behind_as_well_as_the_new_one():
    """Two tables, both named. A reader shown only `stg_events` learns a new
    file exists and nothing about the one still standing beside it, which is
    the half of the fact that causes the incident."""
    plain = _rename(convert(RENAMED)).plain_reason
    assert plain, "the rename Decision carries no plain reason"
    assert "stg_events" in plain, plain
    assert names(plain, "events"), plain


def test_the_plain_cutover_says_the_original_is_left_as_it_is_and_still_read():
    """The two claims that refute the migration reading, each required of the
    ORIGINAL table rather than of the text as a whole -- a text asserting them
    about `stg_events` would be saying something else entirely."""
    plain = _rename(convert(RENAMED)).plain_reason
    assert LEFT_ALONE.asserted_in(plain), plain
    assert STILL_READ.asserted_in(plain), plain


def test_the_plain_cutover_says_repointing_the_readers_is_the_reader_s_own_work():
    """The consequence of the two claims above, and the one a reader acts on.
    Without it the text describes a situation and leaves the reader with no
    reason to think it is theirs to resolve."""
    plain = _rename(convert(RENAMED)).plain_reason
    assert REPOINTING_IS_MANUAL.asserted_in(plain), plain


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
    the model was renamed at all -- and appending the cutover must not cost
    the prefix evidence that answers it."""
    decision = _rename(convert(RENAMED))
    reason = decision.reason
    prefix, evidence = _prefix_convention()
    assert "stg_events" in reason, reason
    assert names(reason, "events"), reason
    assert _keeps_the_convention(reason, prefix, evidence), reason
    assert LEFT_ALONE.asserted_in(reason), reason
    assert STILL_READ.asserted_in(reason), reason
    # All three claims, of BOTH registers -- not the plain one alone. The
    # filter caveat's tests already held both registers to their manual-work
    # claim and this file held only the plain one, so reinstating "this
    # conversion has not done" in the dbt reason passed the whole suite
    # (mutation P4). That asymmetry between two files checking the same thing
    # is what the shared claims module exists to remove.
    assert REPOINTING_IS_MANUAL.asserted_in(reason), reason
    assert reason != decision.plain_reason


def test_keeping_the_word_prefix_is_not_keeping_the_convention():
    """R3, held as a pin. The reason it defeated the earlier assertion is that
    `"stg_" in reason` is a strict consequence of `"stg_events" in reason` one
    line above it -- the eighth non-discriminating guard this branch has
    produced, in the file whose own docstring says it does not repeat that
    mistake."""
    prefix, evidence = _prefix_convention()
    assert "prefix" in R3_REASON_WITHOUT_THE_EVIDENCE
    assert "stg_" in R3_REASON_WITHOUT_THE_EVIDENCE
    assert not _keeps_the_convention(R3_REASON_WITHOUT_THE_EVIDENCE, prefix, evidence)
    # Not vacuous in the other direction: the shipped reason does keep it.
    assert _keeps_the_convention(_rename(convert(RENAMED)).reason, prefix, evidence)


def test_a_model_already_carrying_the_prefix_is_not_renamed_and_claims_no_cutover():
    """No rename, no cutover to warn about, and no Decision inventing one. The
    model assertion is what keeps this from passing for the wrong reason: a
    conversion that produced nothing at all would also record no rename."""
    change = convert(ALREADY_PREFIXED)
    assert [model.name for model in change.models] == ["stg_events"]
    assert not [d for d in change.decisions if d.key.startswith("assemble.rename.")]
    assert not [d for d in change.decisions if d.plain_reason]
