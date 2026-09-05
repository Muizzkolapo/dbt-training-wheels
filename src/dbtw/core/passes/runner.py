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

# Order is load-bearing: pairing consumes truncate+insert before building.
# grants_pass and drop_ddl_pass are deliberately NOT in this tuple — see
# run_passes. grants attach to drafts that must already exist, and a
# tier-2 pass (e.g. merge_pass) can still be the one that creates a draft
# a GRANT targets, so grants_pass has to wait until tier 2 has run too.
TIER1_PASSES: tuple[Pass, ...] = (
    truncate_insert_pass,
    build_models_pass,
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
    # grants_pass runs here, after both tiers, not folded into TIER1_PASSES:
    # a GRANT on a table that only a tier-2 pass builds (e.g. a MERGE target
    # converted by merge_pass) must see that draft to attach to — matching
    # it before tier 2 runs would drop the GRANT with a "this conversion
    # doesn't create it" Decision that is false, since the conversion does
    # create it, two passes later. It runs before drop_ddl_pass rather than
    # after because its only dependency — every draft that will ever exist —
    # is already satisfied the instant tier 2 finishes; there is no reason
    # to make it wait any longer.
    state = grants_pass(state)
    # drop_ddl_pass runs last, after both tiers and after grants_pass, not
    # folded into TIER1_PASSES: tier1's truncate_insert_pass defers a
    # column-list TRUNCATE+INSERT pair to tier 2's truncate_insert_columns_pass
    # by deleting its own lookup entry and leaving both statements pending,
    # with no mark distinguishing a deferred TRUNCATE from a genuinely solo
    # one. Dropping "solo" TRUNCATEs before tier 2 gets a chance to claim that
    # pair would strand its INSERT half with no draft and no Decision
    # explaining why. Running last is necessary but not sufficient: tier 2 can
    # decline the pair too (a star projection it cannot map positionally), so
    # drop_ddl_pass re-derives whether an INSERT against the target is still
    # pending rather than inferring from its own position in the pipeline that
    # a surviving TRUNCATE must be solo. Unlike grants_pass, drop_ddl_pass never reads drafts
    # at all, so nothing about grants_pass moving earlier changes what it
    # sees; it stays the pipeline's true terminal step, a pending-statement
    # cleanup that depends on everything else — including grants_pass —
    # having already run.
    return drop_ddl_pass(state)
