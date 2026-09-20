"""Data shapes for assembling drafts into a dbt project change. No I/O, no logic."""

from __future__ import annotations

from dataclasses import dataclass

from dbtw.core.assemble.variables import Variable
from dbtw.core.ingest.types import ClassifiedStatement
from dbtw.core.passes.types import Decision, ModelDescription, SchemaTest


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
    # Statements that shaped this model without being what it was built from
    # -- a GRANT folded into its config. See `ModelDraft.folded_indices`.
    folded_indices: tuple[int, ...] = ()
    incremental_strategy: str | None = None  # None means "not incremental"
    unique_key: tuple[str, ...] = ()  # empty means no unique key
    # Labels so models can be run in groups later -- `dbt run --select
    # tag:finance`. Empty unless a reader asked for them: a tag is a name for
    # a set somebody intends to run together, and no reading of SQL knows
    # what those sets are.
    tags: tuple[str, ...] = ()


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
    # The dbt tests this run's answers asked for, each naming the final model
    # name it belongs to. Empty unless an answer chose an option that declares
    # one -- a test nobody asked for is a claim about the data nobody made.
    tests: tuple[SchemaTest, ...] = ()
    # What each model is for, in the reader's own words, naming the final
    # model name it belongs to. Empty unless a reader wrote one: what a model
    # is *for* is not derivable from the SQL that builds it, so this engine
    # carries a description and never invents one.
    descriptions: tuple[ModelDescription, ...] = ()
