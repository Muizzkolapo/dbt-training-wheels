"""Data shapes for the pass pipeline. No I/O, no logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dbtw.core.ingest.types import ClassifiedStatement

Tier = Literal[1, 2, 3]


@dataclass(frozen=True, slots=True)
class Option:
    """One answer a Tier-2 question offers, and what taking it does.

    `label` is the answer as both the report and a button name it; `effect`
    is what dbt will do if it is taken, in dbt's own terms. The effect is
    written here rather than by a consumer because the report and the web
    UI render from the same records and must not explain a choice
    differently — see RFC section 9.

    `columns_prompt` is the extra input the option cannot settle without,
    named in the wording every consumer shows above the field it asks for
    it in — "merge on a unique key" says nothing about which key. Empty
    means the label settles the choice by itself, and columns handed to
    such an option are refused rather than discarded. It carries the
    requirement and the wording in one field for the same reason `effect`
    is written here: a bare boolean would leave every consumer inventing
    its own prompt, which is the label-copying this record exists to
    remove, one step removed.
    """

    label: str
    effect: str
    columns_prompt: str = ""


def append_option() -> Option:
    """The 'append every row' answer, worded once for every question that offers it."""
    return Option(
        label="append every row",
        effect=(
            "Every run re-inserts everything this model selects. Rows already in the "
            "table stay where they are, so a second run duplicates them unless the "
            "SELECT filters to new rows itself."
        ),
    )


def merge_option(keys: tuple[str, ...] = ()) -> Option:
    """The 'merge on a key' answer. Names the keys when they are known — read
    off a MERGE's ON clause — and stays generic when the user has yet to pick.
    """
    if not keys:
        return Option(
            label="merge on a unique key",
            effect=(
                "Each run updates the row whose key matches and inserts the rows that "
                "match nothing. Needs a column that identifies a row uniquely — a merge "
                "with an empty unique_key fails at dbt run time."
            ),
            # The one option whose label leaves its key unsaid, so the one
            # option that needs columns supplied with the answer.
            columns_prompt="the column(s) that identify a row uniquely",
        )
    named = ", ".join(keys)
    return Option(
        label=f"merge on {named}",
        effect=(
            f"Updates the row whose {named} matches, with every column this model "
            "selects, and inserts rows matching none."
        ),
    )


def inline_option() -> Option:
    """The 'inline the literal' answer to a script variable's question."""
    return Option(
        label="inline the literal value",
        effect=(
            "The literal from the source SQL is spliced into the model body. The "
            "model stops taking the value at run time and always uses this one."
        ),
    )


def var_option(name: str = "") -> Option:
    """The 'keep it a dbt var' answer. Names the var when the variable it is
    offered for is known, and stays generic when it is not -- the same
    keys-known/keys-unknown shape `merge_option` uses.
    """
    called = f"var('{name}')" if name else "var() with the variable's own name"
    return Option(
        label="keep as a dbt var",
        effect=(
            f"The value stays a run-time parameter: the model calls {called} "
            "and dbt supplies it per run, so it can differ between environments."
        ),
    )


@dataclass(frozen=True, slots=True)
class Subject:
    """What a Tier-2 question is about, as data rather than as prose.

    `table` is the target's name as the source script spelled it -- the
    spelling a user will recognise, NOT `naming.target_key`'s casefolded
    identity triple, which exists for comparison and would show them a name
    their script does not contain.

    `columns` are the columns the question turns on: the key a MERGE's ON
    clause already names, or empty when the question is precisely which
    column to use. Empty means "none known", never "none exist".

    A consumer needs these to offer a check query, to label a column input,
    or to build a worked example. Recovering them by parsing `question`
    would make every consumer a parser of our own prose.
    """

    table: str
    columns: tuple[str, ...] = ()


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
    chosen: str = ""  # Tier-2 only: the label of the option that stands
    options: tuple[Option, ...] = ()  # Tier-2 only: every option, chosen included
    subject: Subject | None = None  # Tier-2 questions only: what it is about


@dataclass(frozen=True, slots=True)
class Answer:
    """A user's reply to one Tier-2 question.

    `label` names the `Option` taken. `columns` carries the extra input an
    option needs when its label alone does not settle it -- "merge on a unique
    key" says nothing about which key.
    """

    label: str
    columns: tuple[str, ...] = ()


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
