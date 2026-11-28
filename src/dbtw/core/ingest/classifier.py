"""Maps each RawStatement to exactly one StatementKind.

Classification is total: parse failure is kind="unsupported" with the error
as the reason. Never raises for statement content.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError

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
        return ClassifiedStatement(
            raw=raw, kind="unsupported", reason=_unparseable_reason(raw, exc, dialect)
        )
    kind, reason = _classify_node(node)
    return ClassifiedStatement(raw=raw, kind=kind, reason=reason)


def classify_statements(result: IngestResult) -> tuple[ClassifiedStatement, ...]:
    return tuple(classify(raw, result.dialect) for raw in result.statements)


# sqlglot underlines the offending token with terminal escape codes. A report
# and a web page show them as raw text.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Some sqlglot messages hold the repr of a Token object. The reader needs only
# its text. SENTINEL is the token sqlglot puts after the last real token.
_TOKEN_REPR = re.compile(r"<Token token_type: TokenType\.(\w+), text: (.*?), line: .*?>")
# Other messages hold the repr of a Python class. The reader needs only its
# name: "missing for <class 'sqlglot.expressions.core.EQ'>" becomes "missing
# for EQ".
_CLASS_REPR = re.compile(r"<class '(?:[\w.]+\.)?(\w+)'>")
_POSITION = re.compile(r"\. Line \d+, Col: \d+\.?$")
# A control byte left after the escape codes are removed. The token is text
# from the SQL file, so it can hold one, and a terminal reads it as a code.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _unparseable_reason(raw: RawStatement, exc: SqlglotError, dialect: str | None) -> str:
    """Why sqlglot could not parse `raw`, as sentences for the reader.

    sqlglot counts the line from the top of the statement. `raw.line_start`
    is the file line of the first character of the statement, so the sum
    gives the file line. The column is given only on a later line of the
    statement. Its first line can start after another statement, so a column
    on that line is not always the column in the file.
    """
    error = exc.errors[0] if isinstance(exc, ParseError) and exc.errors else None
    if error is not None and error.get("line") and error.get("highlight"):
        raw_token = str(error["highlight"])
        line = int(error["line"])
        where = f"line {raw.line_start + line - 1}"
        if line > 1 and "\n" not in raw_token:
            where += f", column {int(error['col']) - len(raw_token) + 1}"
        token = _clean(raw_token)
        if token:
            first = f"This tool could not parse the token {_code(token)} at {where}."
        else:
            first = f"This tool could not parse the statement at {where}."
        detail = str(error.get("description") or "")
    else:
        # A tokenizer error has no line or token. The line where the
        # statement starts is known, so the reader still has a place to look.
        first = f"This tool could not parse the statement that starts at line {raw.line_start}."
        detail = _ANSI.sub("", str(exc)).strip().splitlines()[0] if str(exc).strip() else ""
    detail = _TOKEN_REPR.sub(_token_text, _CLASS_REPR.sub(r"\1", _clean(detail)))
    detail = _POSITION.sub("", detail).strip()
    parts = [first]
    if detail:
        parts.append(f"The sqlglot error is {_code(detail)}.")
    # A plain typo fails to parse too, so the dialect is a possible cause and
    # not a known one. `dbtw convert` and `dbtw web` both take --dialect, and
    # the browser has no field for it, so the advice is to start dbtw again.
    if dialect is None:
        parts.append(
            "This tool used no dialect. "
            "If the SQL is correct, it may use the syntax of one database. "
            "To parse that syntax, start dbtw again with the --dialect option, "
            "for example `--dialect postgres`."
        )
    else:
        parts.append(
            f"This tool parsed the SQL as the {dialect} dialect. "
            "If a different database runs this SQL, "
            "start dbtw again with its name in the --dialect option."
        )
    return " ".join(parts)


def _token_text(match: re.Match[str]) -> str:
    return "the end of the statement" if match.group(1) == "SENTINEL" else match.group(2)


def _clean(text: str) -> str:
    """`text` with no terminal escape codes and no control bytes."""
    return _CONTROL.sub("", _ANSI.sub("", text))


def _code(text: str) -> str:
    """`text` as one code span. A backtick inside it would end the span."""
    return "`" + text.replace("`", "'") + "`"


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
    if isinstance(node, exp.Revoke):
        return "grant", "REVOKE manages permissions"
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
        if isinstance(node.expression, exp.Query):
            return "create_view", "CREATE VIEW from a query"
        return "ddl_other", "CREATE VIEW without a query body (schema-only DDL)"
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
