"""Runs the ordered pass pipeline over classified statements."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from dbtw.core.ingest.types import ClassifiedStatement
from dbtw.core.passes.tier1 import (
    build_models_pass,
    drop_ddl_pass,
    drop_session_pass,
    grants_pass,
    truncate_insert_pass,
)
from dbtw.core.passes.tier2 import append_pass, merge_pass, truncate_insert_columns_pass
from dbtw.core.passes.types import PassState

Pass = Callable[[PassState], PassState]

# Order is load-bearing: pairing consumes truncate+insert before building;
# grants attach to drafts that must already exist. drop_ddl_pass is
# deliberately NOT in this tuple — see run_passes.
TIER1_PASSES: tuple[Pass, ...] = (
    truncate_insert_pass,
    build_models_pass,
    grants_pass,
    drop_session_pass,
)

# Order is load-bearing within tier 2 too: the column-list TRUNCATE+INSERT
# pairing must be consumed before append_pass could mistake its insert for an
# append — the same hazard, one tier down, that slice 3's follow-up #5
# warned about for tier 1's own bare-pair/build ordering.
TIER2_PASSES: tuple[Pass, ...] = (
    truncate_insert_columns_pass,
    merge_pass,
    append_pass,
)


def run_passes(classified: Sequence[ClassifiedStatement], dialect: str | None) -> PassState:
    state = PassState(
        pending=tuple(enumerate(classified)), drafts=(), decisions=(), dialect=dialect
    )
    for tier1_pass in TIER1_PASSES:
        state = tier1_pass(state)
    for tier2_pass in TIER2_PASSES:
        state = tier2_pass(state)
    # drop_ddl_pass runs last, after both tiers, not folded into TIER1_PASSES:
    # tier1's truncate_insert_pass defers a column-list TRUNCATE+INSERT pair
    # to tier 2's truncate_insert_columns_pass by deleting its own lookup
    # entry and leaving both statements pending, with no mark distinguishing
    # a deferred TRUNCATE from a genuinely solo one. Dropping "solo" TRUNCATEs
    # before tier 2 gets a chance to claim that pair would strand its INSERT
    # half with no draft and no Decision explaining why — drop_ddl_pass's own
    # "no surviving INSERT pair" reasoning is only true once every pass that
    # could still pair a TRUNCATE has already run.
    return drop_ddl_pass(state)
