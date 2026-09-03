"""Maps each RawStatement to exactly one StatementKind.

Classification is total: parse failure is kind="unsupported" with the error
as the reason. Never raises for statement content.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbtw.core.ingest.types import ClassifiedStatement, RawStatement, StatementKind


def classify(raw: RawStatement, dialect: str | None = None) -> ClassifiedStatement:
    try:
        node = sqlglot.parse_one(raw.text, read=dialect)
    except SqlglotError as exc:
        return ClassifiedStatement(raw=raw, kind="unsupported", reason=f"could not parse: {exc}")
    kind, reason = _classify_node(node)
    return ClassifiedStatement(raw=raw, kind=kind, reason=reason)


def _classify_node(node: exp.Expr) -> tuple[StatementKind, str]:
    if isinstance(node, exp.Select):
        if node.args.get("into") is not None:
            return "create_table_as", "SELECT INTO creates a table from a query"
        return "select", "parsed as a query"
    if isinstance(node, exp.Create):
        return _classify_create(node)
    if isinstance(node, exp.Insert):
        if isinstance(node.expression, exp.Select):
            return "insert_select", "INSERT from a SELECT"
        if isinstance(node.expression, exp.Values):
            return "unsupported", "INSERT ... VALUES has no catalog entry yet"
        return "unsupported", "INSERT without a SELECT source"
    if isinstance(node, exp.Merge):
        return "merge", "MERGE upserts into a target"
    return "unsupported", f"no classification rule for {type(node).__name__}"


def _classify_create(node: exp.Create) -> tuple[StatementKind, str]:
    kind = (node.kind or "").upper()
    if kind == "TABLE":
        if isinstance(node.expression, exp.Select):
            return "create_table_as", "CREATE TABLE ... AS SELECT"
        return "ddl_other", "CREATE TABLE without a SELECT (schema-only DDL)"
    if kind == "VIEW":
        return "create_view", "CREATE VIEW from a query"
    return "ddl_other", f"CREATE {kind or 'object'}"
