"""The filter an incremental model keeps, and what keeping it costs.

Choosing merge fixes duplication and leaves a literal `WHERE occurred_at >
'2024-01-01'` exactly where it was. dbt does not narrow that condition between
runs and this conversion does not wrap it in an `is_incremental()` guard --
deliberately, since nothing here can tell a watermark from an ordinary
business filter, and synthesizing one around the wrong condition would change
which rows the model produces. So the model re-reads everything matching it on
every run, and a row that does not match is selected by no run at all.

This was the warehouse engineer's single change request in BOTH persona rounds
-- the only finding to survive a full rebuild unaddressed (spec section 11.6).
It is not fixed here. What is fixed is that the walk no longer says nothing
about it: the gap gets a Decision, in both registers, naming the condition it
is about.

The claim is deliberately general rather than date-shaped. A `WHERE status =
'complete'` is a fixed condition on exactly the same terms, and a row that
later becomes complete IS picked up, because the whole source is re-read --
so "a late row is never picked up" is true of a date cutoff and false in
general. The date case is stated as the illustration it is.
"""

from __future__ import annotations

from tests.unit.assemble.helpers import convert
from tests.unit.passes.test_plain_register import JARGON

FILTER = "occurred_at > '2024-01-01'"

APPEND_WITH_FILTER = (
    f"INSERT INTO events SELECT event_id, occurred_at FROM raw.events WHERE {FILTER};\n"
)
APPEND_WITHOUT_FILTER = "INSERT INTO events SELECT event_id, occurred_at FROM raw.events;\n"
REBUILD_WITH_FILTER = f"CREATE TABLE events AS SELECT event_id FROM raw.events WHERE {FILTER};\n"
# Two drafts folding onto one final name: `events` takes the staging prefix
# and lands on the name the second statement already writes. A dbt model is
# one file, so the assembler keeps the later draft and drops the earlier one --
# and the dropped one is the filtered one, so nothing may be said about a
# filter in a file that was never written.
COLLIDING_WITH_ONE_FILTER = (
    f"INSERT INTO events SELECT event_id, occurred_at FROM raw.events WHERE {FILTER};\n"
    "INSERT INTO stg_events SELECT event_id, occurred_at FROM raw.events;\n"
)
MERGE_WITH_FILTER = (
    "MERGE INTO dim_customers AS t\n"
    f"USING (SELECT customer_id, name FROM raw.customers WHERE {FILTER}) AS s\n"
    "ON t.customer_id = s.customer_id\n"
    "WHEN MATCHED THEN UPDATE SET t.name = s.name\n"
    "WHEN NOT MATCHED THEN INSERT (customer_id, name) VALUES (s.customer_id, s.name);\n"
)

# Two claims, each required of one sentence rather than of the text as a
# whole. Both are conjunctions: a sentence has to carry a word from each half,
# which is what stops "every run" alone -- a phrase this text could hardly
# avoid -- from satisfying the rescan claim by accident.
_RESCAN = (("every run", "each run", "run after that"), ("everything", "whole", "all of"))
_NEVER_SELECTED = (("never", "no run at all", "not by any"), ("match", "satisfy"))
_BY_HAND = ("by hand", "yourself", "someone has to", "manually", "manual")


def _caveats(change):
    return [d for d in change.decisions if d.key.startswith("assemble.incremental_filter.")]


def _the_caveat(change):
    (decision,) = _caveats(change)
    return decision


def _sentences(text: str) -> list[str]:
    import re

    return [part.strip() for part in re.split(r"(?<=[.;:])\s+", text) if part.strip()]


def _claiming(text: str, claim: tuple[tuple[str, ...], ...]) -> list[str]:
    return [
        sentence
        for sentence in _sentences(text)
        if all(any(word in sentence.lower() for word in half) for half in claim)
    ]


def test_the_claim_matcher_needs_both_halves_of_a_sentence():
    """The conjunction, shown to discriminate: a sentence carrying only the
    half that is hard to avoid does not count as the claim."""
    assert _claiming("Every run re-reads everything matching it.", _RESCAN)
    assert not _claiming("Every run inserts the rows it selects.", _RESCAN)


def test_an_incremental_model_keeping_the_script_s_filter_gets_a_caveat():
    """The Decision exists, is about this model, and quotes the condition it
    is about rather than describing it -- a caveat naming no condition sends
    the reader back to the SQL to guess which line it meant."""
    caveat = _the_caveat(convert(APPEND_WITH_FILTER))
    assert "stg_events" in caveat.action, caveat.action
    assert FILTER in caveat.action, caveat.action
    assert FILTER in caveat.reason, caveat.reason
    assert FILTER in caveat.plain_reason, caveat.plain_reason


def test_the_caveat_says_the_whole_source_is_re_read_and_a_non_matching_row_never_taken():
    """The two facts the gap consists of, in both registers. The first is what
    the run costs; the second is which rows the model can ever contain, and it
    is the half the warehouse engineer asked for twice."""
    caveat = _the_caveat(convert(APPEND_WITH_FILTER))
    for register in (caveat.reason, caveat.plain_reason):
        assert _claiming(register, _RESCAN), register
        assert _claiming(register, _NEVER_SELECTED), register


def test_the_caveat_says_narrowing_the_filter_is_the_reader_s_own_work():
    """The conversion did not do it and will not. Without this the Decision
    reads as a description of dbt rather than as something outstanding."""
    caveat = _the_caveat(convert(APPEND_WITH_FILTER))
    assert [s for s in _sentences(caveat.plain_reason) if any(w in s.lower() for w in _BY_HAND)]
    assert [s for s in _sentences(caveat.reason) if any(w in s.lower() for w in _BY_HAND)]


def test_the_caveat_promises_no_guard_the_conversion_did_not_write():
    """The escape from this gap is a dbt `is_incremental()` guard around the
    filter, and this conversion deliberately writes none -- it cannot tell a
    watermark from an ordinary business filter. The dbt-native register may
    name the mechanism, because its reader can check the model file; the plain
    register may not hand a reader a construct with nothing to type it into.
    """
    caveat = _the_caveat(convert(APPEND_WITH_FILTER))
    assert "is_incremental" in caveat.reason, caveat.reason
    assert "is_incremental" not in caveat.plain_reason, caveat.plain_reason


def test_the_caveat_is_recorded_at_the_tier_a_reader_meets_caveats_at():
    """Tier decides where a reader meets this. Section 5.2 collapses Tier 1
    into "mechanical changes, with a count" -- the right treatment for a
    rename and the wrong one for a gap someone has to act on, and every other
    caveat this tool records is Tier 2 for the same reason."""
    change = convert(APPEND_WITH_FILTER)
    assert _the_caveat(change).tier == 2
    # Not vacuous: the rename in the same conversion is Tier 1, so this is not
    # reading back a constant the assembler stamps on everything it records.
    renames = [d for d in change.decisions if d.key.startswith("assemble.rename.")]
    assert renames and all(d.tier == 1 for d in renames)


def test_the_caveat_plain_register_uses_no_dbt_vocabulary():
    plain = _the_caveat(convert(APPEND_WITH_FILTER)).plain_reason
    assert plain, "the caveat carries no plain reason"
    found = [term for term in JARGON if term in plain.lower()]
    assert not found, f"the plain caveat uses {found}: {plain}"


def test_the_caveat_reaches_a_merge_derived_model_too():
    """The gap is a property of an incremental model carrying a fixed filter,
    not of the pass that built it. A MERGE whose USING subquery carries the
    same WHERE produces the same model and the same cost, and a caveat emitted
    only where the append pass happens to look would be half a fix."""
    change = convert(MERGE_WITH_FILTER)
    ((model,), caveat) = (change.models, _the_caveat(change))
    assert model.incremental_strategy == "merge"
    assert model.name in caveat.action, caveat.action
    assert FILTER in caveat.reason, caveat.reason


def test_an_incremental_model_with_no_filter_gets_no_caveat():
    """Nothing to warn about: the model already selects its whole source
    every run and says so. A caveat here would describe a line the model
    does not contain."""
    change = convert(APPEND_WITHOUT_FILTER)
    assert [m.incremental_strategy for m in change.models] == ["append"]
    assert not _caveats(change)


def test_a_draft_dropped_for_a_name_collision_leaves_no_caveat_behind():
    """Two drafts, one final name, one file: the assembler keeps the later
    draft and drops the earlier. The dropped one carried the filter, so a
    caveat about it would describe a line in a model this conversion did not
    write -- the same reason its rename and placement Decisions are discarded
    with it."""
    change = convert(COLLIDING_WITH_ONE_FILTER)
    ((model,),) = (change.models,)
    # Not vacuous: this is the proof the collision really happened and that
    # the surviving model is the unfiltered one.
    assert [d.key for d in change.decisions if d.key.startswith("assemble.collision.")] == [
        "assemble.collision.events"
    ]
    assert FILTER not in model.body
    assert not _caveats(change)


def test_a_full_rebuild_carrying_the_same_filter_gets_no_caveat():
    """A table materialization is rebuilt from nothing on every run by
    design, so re-reading everything the filter matches is what it is for --
    there is no narrowing anyone was promised and none withheld. The
    assertion on the strategy is what keeps this from passing because the
    conversion produced something else entirely."""
    change = convert(REBUILD_WITH_FILTER)
    ((model,),) = (change.models,)
    assert model.incremental_strategy is None and model.materialization == "table"
    assert FILTER in model.body
    assert not _caveats(change)
