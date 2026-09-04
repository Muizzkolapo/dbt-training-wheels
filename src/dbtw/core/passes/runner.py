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
from dbtw.core.passes.types import PassState

Pass = Callable[[PassState], PassState]

# Order is load-bearing: pairing consumes truncate+insert before building;
# grants attach to drafts that must already exist; drops run last so solo
# truncates survive until the DDL drop.
TIER1_PASSES: tuple[Pass, ...] = (
    truncate_insert_pass,
    build_models_pass,
    grants_pass,
    drop_session_pass,
    drop_ddl_pass,
)


def run_passes(classified: Sequence[ClassifiedStatement], dialect: str | None) -> PassState:
    state = PassState(
        pending=tuple(enumerate(classified)), drafts=(), decisions=(), dialect=dialect
    )
    for tier1_pass in TIER1_PASSES:
        state = tier1_pass(state)
    return state
