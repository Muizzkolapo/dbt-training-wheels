"""Data shapes for SQL ingestion and classification. No I/O, no logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StatementKind = Literal[
    "select",
    "create_table_as",
    "create_view",
    "insert_select",
    "merge",
    "delete",
    "update",
    "truncate",
    "variable",
    "session",
    "grant",
    "ddl_other",
    "procedural",
    "unsupported",
]


class UnknownDialectError(Exception):
    """The given dialect name is not one sqlglot knows."""


@dataclass(frozen=True, slots=True)
class RawStatement:
    """One statement as found in the input, verbatim (leading comments included)."""

    source_file: str  # path as given to ingest(), posix
    index: int  # 0-based position within its file
    text: str  # original text, stripped of surrounding whitespace, no trailing ;
    line_start: int  # 1-based, inclusive
    line_end: int  # 1-based, inclusive


@dataclass(frozen=True, slots=True)
class IngestResult:
    statements: tuple[RawStatement, ...]
    dialect: str | None  # the dialect statements were tokenized with
    warnings: tuple[str, ...]  # e.g. "no dialect specified", skipped-file notes


@dataclass(frozen=True, slots=True)
class ClassifiedStatement:
    """A RawStatement with exactly one kind. reason is always populated."""

    raw: RawStatement
    kind: StatementKind
    reason: str  # why this kind; for "unsupported", the parse error or gap
