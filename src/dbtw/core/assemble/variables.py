"""Data shapes for variables. No I/O, no logic."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Variable:
    """A dbt variable reference found in model SQL."""

    name: str  # the variable name, e.g. "start_date"
    default_sql: str | None  # SQL expression for the default, or None if uninitialized
    source_file: str  # where the variable was referenced
    line_start: int  # source line number
