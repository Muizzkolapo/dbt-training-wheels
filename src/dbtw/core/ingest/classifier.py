"""Maps each RawStatement to exactly one StatementKind.

Classification is total: parse failure is kind="unsupported" with the error
as the reason. Never raises for statement content.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbtw.core.ingest.types import (
    ClassifiedStatement,
    IngestResult,
    RawStatement,
    StatementKind,
)


def classify(raw: RawStatement, dialect: str | None = None) -> ClassifiedStatement:
    try:
        node = sqlglot.parse_one(raw.text, read=dialect)
    except SqlglotError as exc:
        return ClassifiedStatement(raw=raw, kind="unsupported", reason=f"could not parse: {exc}")
    kind, reason = _classify_node(node)
    return ClassifiedStatement(raw=raw, kind=kind, reason=reason)


def classify_statements(result: IngestResult) -> tuple[ClassifiedStatement, ...]:
    return tuple(classify(raw, result.dialect) for raw in result.statements)


def _classify_node(node: exp.Expr) -> tuple[StatementKind, str]:
    if isinstance(node, exp.Select):
        if node.args.get("into") is not None:
            return "create_table_as", "SELECT INTO creates a table from a query"
        return "select", "parsed as a query"
    if isinstance(node, exp.Query):
        return "select", "parsed as a query"
    if isinstance(node, exp.Create):
        return _classify_create(node)
    if isinstance(node, exp.Insert):
        if isinstance(node.expression, exp.Query):
            return "insert_select", "INSERT from a SELECT"
        if isinstance(node.expression, exp.Values):
            return "unsupported", "INSERT ... VALUES has no catalog entry yet"
        return "unsupported", "INSERT without a SELECT source"
    if isinstance(node, exp.Merge):
        return "merge", "MERGE upserts into a target"
    if isinstance(node, exp.Delete):
        return "delete", "DELETE removes rows"
    if isinstance(node, exp.Update):
        return "update", "UPDATE mutates rows in place"
    if isinstance(node, exp.TruncateTable):
        return "truncate", "TRUNCATE empties a table"
    if isinstance(node, exp.Grant):
        return "grant", "GRANT manages permissions"
    if isinstance(node, exp.Alter):
        if str(node.args.get("kind") or "").upper() == "SESSION":
            return "session", "ALTER SESSION sets connection state"
        return "ddl_other", f"ALTER {node.args.get('kind') or 'object'}"
    if isinstance(node, exp.Drop):
        return "ddl_other", "DROP removes an object"
    if isinstance(node, exp.Declare):
        return "variable", "DECLARE introduces a script variable"
    if isinstance(node, exp.Set):
        return _classify_set(node)
    if isinstance(node, exp.Use):
        return "session", "USE selects a database/warehouse"
    if isinstance(node, exp.Execute):
        return "procedural", "EXEC invokes a procedure"
    if isinstance(node, exp.Command):
        return "unsupported", f"sqlglot could not parse this syntax: {node.sql()!r}"
    if isinstance(node, exp.Copy):
        return "unsupported", "COPY loads files; no dbt model equivalent"
    if isinstance(node, (exp.Transaction, exp.Commit, exp.Rollback)):
        return "unsupported", "transaction control; dbt owns transaction boundaries"
    if isinstance(node, exp.IfBlock):
        return "procedural", "IF/ELSE control flow is procedural code"
    return "unsupported", f"no classification rule for {type(node).__name__}"


def _classify_create(node: exp.Create) -> tuple[StatementKind, str]:
    kind = (node.kind or "").upper()
    if kind == "TABLE":
        if isinstance(node.expression, exp.Query):
            return "create_table_as", "CREATE TABLE ... AS SELECT"
        return "ddl_other", "CREATE TABLE without a SELECT (schema-only DDL)"
    if kind == "VIEW":
        return "create_view", "CREATE VIEW from a query"
    if kind in ("PROCEDURE", "FUNCTION"):
        return "procedural", f"CREATE {kind} is procedural code"
    return "ddl_other", f"CREATE {kind or 'object'}"


def _classify_set(node: exp.Set) -> tuple[StatementKind, str]:
    for item in node.expressions:
        eq = item.this
        target = eq.this if isinstance(eq, exp.EQ) else None
        if isinstance(target, exp.Parameter):
            return "variable", "SET assigns a script variable"
        if item.args.get("kind") == "VARIABLE":
            return "variable", "SET assigns a script variable"
    return "session", "SET changes a session setting"
