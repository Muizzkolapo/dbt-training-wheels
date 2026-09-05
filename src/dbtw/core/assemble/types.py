"""Data shapes for assembling drafts into a dbt project change. No I/O, no logic."""

from __future__ import annotations

from dataclasses import dataclass

from dbtw.core.assemble.variables import Variable
from dbtw.core.ingest.types import ClassifiedStatement
from dbtw.core.passes.types import Decision


@dataclass(frozen=True, slots=True)
class TableRef:
    """A table referenced by a model body, as written in the source SQL."""

    catalog: str  # "" when unqualified
    db: str  # schema; "" when unqualified
    name: str


@dataclass(frozen=True, slots=True)
class AssembledModel:
    """A model with its final name, placement, and dependencies resolved."""

    name: str  # final name, target-project prefix applied
    path: str  # project-root-relative posix path of the .sql file
    body: str
    materialization: str | None  # None = matches the layer default, omit from config
    grants: tuple[tuple[str, tuple[str, ...]], ...]
    layer: str  # the target project's layer name it was placed in
    depends_on: tuple[str, ...]  # final names of other models in this change
    leading_comments: tuple[str, ...]
    source_indices: tuple[int, ...]
    incremental_strategy: str | None = None  # None means "not incremental"
    unique_key: tuple[str, ...] = ()  # empty means no unique key


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """One source table to declare in sources.yml."""

    source_name: str
    schema: str
    table: str


@dataclass(frozen=True, slots=True)
class ProjectChange:
    """Everything the emitter and the report need. Models are in dependency order."""

    models: tuple[AssembledModel, ...]
    sources: tuple[SourceEntry, ...]
    decisions: tuple[Decision, ...]  # pass decisions plus assemble's own
    pending: tuple[tuple[int, ClassifiedStatement], ...]  # later-tier material
    dialect: str | None
    project_name: str
    variables: tuple[Variable, ...] = ()  # variables referenced in the models
