"""Data shapes for the pass pipeline. No I/O, no logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dbtw.core.ingest.types import ClassifiedStatement

Tier = Literal[1, 2, 3]


@dataclass(frozen=True, slots=True)
class Decision:
    """One recorded pass action: what was found, what was done, and why."""

    key: str  # unique per decision, e.g. "tier1.build.etl.sql:4"
    tier: Tier
    action: str  # what the pass did or proposes, in dbt terms
    reason: str  # why — dbt-native explanation (seed for teaching copy)
    source_file: str
    line_start: int
    line_end: int
    question: str = ""  # Tier-2 only: the design question posed to the user
    chosen: str = ""  # Tier-2 only: what the user chose
    alternatives: tuple[str, ...] = ()  # Tier-2 only: alternatives presented


@dataclass(frozen=True, slots=True)
class ModelDraft:
    """A dbt model in the making. Naming/layout finalized at assemble (slice 4)."""

    name: str  # target table identifier (unqualified), as the script spelled it
    qualified_name: str  # dotted catalog.db.name (non-empty parts only); bare name if unqualified
    # naming.target_key of the target this draft was built from: (catalog, db,
    # name), each casefolded unless it was written quoted. `name` and
    # `qualified_name` are the spellings the script used and are what the
    # report and the model file show; this is what decides whether two drafts
    # are the same table. Comparing the spellings instead makes `EVENTS` and
    # `Events` two models, of which the filesystem keeps one.
    identity: tuple[str, str, str]
    body: str  # the SELECT, regenerated pretty; inner comments preserved
    materialization: str  # "table" | "view"; assemble may omit if layer default
    grants: tuple[tuple[str, tuple[str, ...]], ...]  # (privilege, principals)
    source_indices: tuple[int, ...]  # pipeline indices folded into this draft
    leading_comments: tuple[str, ...]  # statement-level comments, no delimiters
    incremental_strategy: str | None = None  # None means "not incremental"
    unique_key: tuple[str, ...] = ()  # empty means no unique key


@dataclass(frozen=True, slots=True)
class PassState:
    """The pipeline's working set. Passes consume pending items and add output."""

    pending: tuple[tuple[int, ClassifiedStatement], ...]  # (pipeline index, stmt)
    drafts: tuple[ModelDraft, ...]
    decisions: tuple[Decision, ...]
    dialect: str | None
