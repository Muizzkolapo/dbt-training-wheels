"""The filter an incremental model keeps, and what keeping it costs.

Choosing merge fixes duplication and leaves a literal `WHERE occurred_at >
'2024-01-01'` exactly where it was. dbt does not narrow that condition between
runs and this conversion writes no guard around it -- deliberately, since
nothing here can tell a watermark from an ordinary business filter, and
guarding the wrong condition would change which rows the model produces. So
the model re-reads everything matching it on every run, and a row that does
not match is selected by no run at all.

This was the warehouse engineer's single change request in BOTH persona rounds
-- the only finding to survive a full rebuild unaddressed (spec section 11.6).
It is not fixed here. What is fixed is that the walk no longer says nothing
about it: the gap gets a Decision, in both registers, naming the conditions it
is about.

The claim is deliberately general rather than date-shaped. A `WHERE status =
'complete'` is a fixed condition on exactly the same terms, and a row that
later becomes complete IS picked up, because the whole source is re-read --
so "a late row is never picked up" is true of a date cutoff and false in
general. The date case is stated as the illustration it is.

Whether a register makes a claim is decided by
`tests/unit/register_claims.py`, shared with the rename cutover's tests. The
first version of this file defined the same manual-work vocabulary as that one
and checked it with a bare substring search, so a blind review reversed the
sentence here that it could not reverse there.
"""

from __future__ import annotations

from tests.unit.assemble.helpers import convert
from tests.unit.passes.test_plain_register import JARGON
from tests.unit.register_claims import NARROWING_IS_MANUAL, NEVER_SELECTED, RESCAN

from dbtw.core.assemble.assembler import _incremental_filter_caveat
from dbtw.core.passes.types import ModelDraft

FILTER = "occurred_at > '2024-01-01'"

APPEND_WITH_FILTER = (
    f"INSERT INTO events SELECT event_id, occurred_at FROM raw.events WHERE {FILTER};\n"
)
APPEND_WITHOUT_FILTER = "INSERT INTO events SELECT event_id, occurred_at FROM raw.events;\n"
REBUILD_WITH_FILTER = f"CREATE TABLE events AS SELECT event_id FROM raw.events WHERE {FILTER};\n"
MERGE_WITH_FILTER = (
    "MERGE INTO dim_customers AS t\n"
    f"USING (SELECT customer_id, name FROM raw.customers WHERE {FILTER}) AS s\n"
    "ON t.customer_id = s.customer_id\n"
    "WHEN MATCHED THEN UPDATE SET t.name = s.name\n"
    "WHEN NOT MATCHED THEN INSERT (customer_id, name) VALUES (s.customer_id, s.name);\n"
)

# The three shapes the first version of this caveat could not see. Each parses
# cleanly and each carries the condition somewhere other than a WHERE hanging
# off the outermost SELECT, which is all the first version looked at. A blind
# review drove all three through the real pipeline and got no caveat and no
# WHERE named anywhere -- the finding that survived two persona rounds,
# missing for three ordinary ways of writing the same query.
NESTED_SHAPES = {
    "a CTE": (
        f"INSERT INTO events WITH recent AS (SELECT event_id, occurred_at FROM raw.events "
        f"WHERE {FILTER}) SELECT event_id, occurred_at FROM recent;\n"
    ),
    "one branch of a UNION": (
        f"INSERT INTO events SELECT event_id, occurred_at FROM raw.events WHERE {FILTER} "
        "UNION ALL SELECT event_id, occurred_at FROM raw.events_archive;\n"
    ),
    "a derived table": (
        "INSERT INTO events SELECT event_id, occurred_at FROM (SELECT event_id, occurred_at "
        f"FROM raw.events WHERE {FILTER}) s;\n"
    ),
}

# Two conditions in one model: a CTE filter and an outer one. Both are fixed
# and both cost the same thing, so the caveat has to name both rather than
# whichever it happens to reach first.
TWO_FILTERS = (
    f"INSERT INTO events WITH recent AS (SELECT event_id, occurred_at FROM raw.events "
    f"WHERE {FILTER}) SELECT event_id, occurred_at FROM recent WHERE event_id > 100;\n"
)


def _caveats(change):
    return [d for d in change.decisions if d.key.startswith("assemble.incremental_filter.")]


def _the_caveat(change):
    (decision,) = _caveats(change)
    return decision


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
        assert RESCAN.asserted_in(register), register
        assert NEVER_SELECTED.asserted_in(register), register


def test_the_caveat_says_narrowing_the_filter_is_the_reader_s_own_work():
    """The conversion did not do it and will not. Without this the Decision
    reads as a description of dbt rather than as something outstanding."""
    caveat = _the_caveat(convert(APPEND_WITH_FILTER))
    for register in (caveat.reason, caveat.plain_reason):
        assert NARROWING_IS_MANUAL.asserted_in(register), register


def test_neither_register_names_a_dbt_word_the_report_cannot_define():
    """The escape from this gap is a dbt `is_incremental()` guard, and this
    conversion writes none. Naming the macro would put a dbt word into the
    report under a heading promising to define the dbt words the report uses
    -- and the glossary cannot take it: every spelling of it contains
    `incremental`, already a term, and no term's spelling may nest inside
    another's. So the category is named and the macro is not, which costs a
    dbt reader nothing they cannot recover from the model file."""
    caveat = _the_caveat(convert(APPEND_WITH_FILTER))
    assert "guard" in caveat.reason, caveat.reason
    assert "is_incremental" not in caveat.reason, caveat.reason
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


def test_the_caveat_reaches_a_condition_that_is_not_on_the_outermost_select():
    """The three shapes the first version missed, each driven through the real
    pipeline. The model is incremental and the condition is in it, so the gap
    is exactly the one the flat case gets a caveat for -- and the flat case is
    the only one of four a reader can rely on writing."""
    for shape, sql in NESTED_SHAPES.items():
        change = convert(sql)
        assert [m.incremental_strategy for m in change.models] == ["append"], shape
        caveat = _the_caveat(change)
        assert FILTER in caveat.action, (shape, caveat.action)
        assert FILTER in caveat.reason, (shape, caveat.reason)
        assert FILTER in caveat.plain_reason, (shape, caveat.plain_reason)


def test_a_model_filtered_twice_has_both_conditions_named():
    """Naming one of two would leave the reader believing the other was
    handled -- the precise thing this Decision exists to prevent. The count in
    the wording is derived from the conditions found, never authored."""
    caveat = _the_caveat(convert(TWO_FILTERS))
    assert FILTER in caveat.reason, caveat.reason
    assert "event_id > 100" in caveat.reason, caveat.reason
    assert "2 WHERE clauses" in caveat.action, caveat.action


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
    change = convert(
        f"INSERT INTO events SELECT event_id, occurred_at FROM raw.events WHERE {FILTER};\n"
        "INSERT INTO stg_events SELECT event_id, occurred_at FROM raw.events;\n"
    )
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


def _draft(body: str) -> ModelDraft:
    return ModelDraft(
        name="events",
        qualified_name="events",
        identity=("", "", "events"),
        body=body,
        materialization="incremental",
        grants=(),
        source_indices=(0,),
        leading_comments=(),
        incremental_strategy="append",
    )


def test_a_body_already_rewritten_into_jinja_yields_no_caveat():
    """The ordering dependency, made to fail loudly rather than go quiet.

    This reads raw SQL, so it has to run before `rewrite_body` -- a rewritten
    body does not parse, `parse_one` raises, and the caveat would vanish with
    nothing saying why. Asserting both halves here means a reordering in
    `assemble` shows up as this test's premise (a rewritten body yields
    nothing) still holding while the pipeline tests above go red, instead of
    as a silent absence.
    """
    raw = f"SELECT event_id FROM raw.events WHERE {FILTER}"
    rewritten = f"SELECT event_id FROM {{{{ source('raw', 'events') }}}} WHERE {FILTER}"
    assert _incremental_filter_caveat(_draft(raw), "stg_events", None), "the premise is wrong"
    assert not _incremental_filter_caveat(_draft(rewritten), "stg_events", None)
