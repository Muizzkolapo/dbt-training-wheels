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

The `kind="VARIABLE"` SET form is consumed for every dialect except
spark/databricks. DuckDB reads such a variable back with an unambiguous
`GETVARIABLE('name')` call (see rewrite.py), but Spark/Databricks has no
such function — a SET VAR-declared session variable is read back by BARE
IDENTIFIER (`SELECT cutoff`), which sqlglot parses as `exp.Column`,
indistinguishable from an ordinary column reference of the same name.
Rewriting that would silently turn a real column into a `var()` call — the
exact silent-garbage class this project exists to avoid. So under
spark/databricks, this form is left pending with a Decision explaining why,
for a human to resolve, instead of being guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbtw.core.ingest.types import ClassifiedStatement
from dbtw.core.passes.types import Decision


@dataclass(frozen=True, slots=True)
class Variable:
    """A dbt variable reference found in model SQL."""

    name: str  # the variable name, e.g. "start_date"
    default_sql: str | None  # SQL expression for the default, or None if uninitialized
    source_file: str  # where the variable was referenced
    line_start: int  # source line number


# Dialects where a SET VAR-declared variable's read-back form (a bare
# identifier) cannot be told apart from a column reference.
_BARE_IDENTIFIER_READBACK_DIALECTS = frozenset({"spark", "databricks"})


def _is_undeferrable_spark_set_var(node: exp.Expr, dialect: str | None) -> bool:
    """True for a `SET VAR`/`SET VARIABLE` statement under spark/databricks —
    the one shape this module must leave pending rather than consume.
    """
    if dialect not in _BARE_IDENTIFIER_READBACK_DIALECTS:
        return False
    if not isinstance(node, exp.Set):
        return False
    return any(item.args.get("kind") == "VARIABLE" for item in node.expressions)


def _spark_deferral_decision(stmt: ClassifiedStatement, index: int) -> Decision:
    raw = stmt.raw
    return Decision(
        key=f"variables.spark_set_var_deferred.{raw.source_file}:{index}",
        tier=2,
        action="left SET VAR/SET VARIABLE statement as written; not converted to a dbt var",
        reason=(
            "Spark/Databricks has no GETVARIABLE function; a SET VAR-declared "
            "session variable is read back by a bare identifier, which parses "
            "identically to a column reference — there is no safe way to tell "
            "them apart, so this statement is left pending for a human to resolve"
        ),
        source_file=raw.source_file,
        line_start=raw.line_start,
        line_end=raw.line_end,
    )


def extract_variables(
    pending: tuple[tuple[int, ClassifiedStatement], ...],
    dialect: str | None,
) -> tuple[tuple[Variable, ...], tuple[int, ...], tuple[Decision, ...]]:
    variables: list[Variable] = []
    consumed: list[int] = []
    decisions: list[Decision] = []
    for index, stmt in pending:
        if stmt.kind != "variable":
            continue
        try:
            node = sqlglot.parse_one(stmt.raw.text, read=dialect)
        except SqlglotError:
            continue
        if _is_undeferrable_spark_set_var(node, dialect):
            decisions.append(_spark_deferral_decision(stmt, index))
            continue
        found = _variables_in(node, stmt.raw.source_file, stmt.raw.line_start)
        if found:
            variables.extend(found)
            consumed.append(index)
    return tuple(variables), tuple(consumed), tuple(decisions)


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
