"""Extracts script variables from DECLARE and SET statements.

Only statements already classified as kind="variable" are considered. A
DECLARE can introduce several variables at once: one DeclareItem per comma
group, and within a group sqlglot's shared-type form (`DECLARE @a, @b INT`)
puts every Parameter in that one DeclareItem's `.this` list, sharing that
item's `kind` and `default` across all of them. A SET assigns a script
variable either via a bare Parameter lhs (`SET @x = 5`) or via the
`kind="VARIABLE"` marker sqlglot attaches for `SET VARIABLE x = 5` (DuckDB)
/ `SET VAR x = 5` (Spark), whose lhs is a plain Column; a session setting
(`SET search_path = analytics`, Column lhs, no VARIABLE marker) is not a
variable and must be ignored without being consumed.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbtw.core.ingest.types import ClassifiedStatement


@dataclass(frozen=True, slots=True)
class Variable:
    """A dbt variable reference found in model SQL."""

    name: str  # the variable name, e.g. "start_date"
    default_sql: str | None  # SQL expression for the default, or None if uninitialized
    source_file: str  # where the variable was referenced
    line_start: int  # source line number


def extract_variables(
    pending: tuple[tuple[int, ClassifiedStatement], ...],
    dialect: str | None,
) -> tuple[tuple[Variable, ...], tuple[int, ...]]:
    variables: list[Variable] = []
    consumed: list[int] = []
    for index, stmt in pending:
        if stmt.kind != "variable":
            continue
        try:
            node = sqlglot.parse_one(stmt.raw.text, read=dialect)
        except SqlglotError:
            continue
        found = _variables_in(node, stmt.raw.source_file, stmt.raw.line_start)
        if found:
            variables.extend(found)
            consumed.append(index)
    return tuple(variables), tuple(consumed)


def _variables_in(node: exp.Expr, source_file: str, line_start: int) -> tuple[Variable, ...]:
    if isinstance(node, exp.Declare):
        return tuple(
            variable
            for item in node.expressions
            for variable in _variables_from_declare_item(item, source_file, line_start)
        )
    if isinstance(node, exp.Set):
        return tuple(
            variable
            for item in node.expressions
            if (variable := _variable_from_set_item(item, source_file, line_start)) is not None
        )
    return ()


def _variables_from_declare_item(
    item: exp.DeclareItem, source_file: str, line_start: int
) -> tuple[Variable, ...]:
    default = item.args["default"]
    default_sql = default.sql() if default is not False else None
    return tuple(
        Variable(
            name=parameter.name,
            default_sql=default_sql,
            source_file=source_file,
            line_start=line_start,
        )
        for parameter in item.this
        # A degenerate parse (e.g. "DECLARE @@@ bad") reaches this point
        # without raising SqlglotError but yields no usable identifier.
        if parameter.name
    )


def _variable_from_set_item(
    item: exp.SetItem, source_file: str, line_start: int
) -> Variable | None:
    eq = item.this
    if not isinstance(eq, exp.EQ):
        return None
    is_script_variable = isinstance(eq.this, exp.Parameter) or item.args.get("kind") == "VARIABLE"
    if not is_script_variable or not eq.this.name:
        return None
    return Variable(
        name=eq.this.name,
        default_sql=eq.expression.sql(),
        source_file=source_file,
        line_start=line_start,
    )
