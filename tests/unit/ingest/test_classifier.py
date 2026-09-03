import pytest

from dbtw.core.ingest import RawStatement
from dbtw.core.ingest.classifier import classify
from dbtw.core.ingest.types import StatementKind


def _raw(text: str) -> RawStatement:
    return RawStatement(source_file="t.sql", index=0, text=text, line_start=1, line_end=1)


def _kind(text: str, dialect: str | None = None) -> str:
    return classify(_raw(text), dialect).kind


def test_select_and_cte_select():
    assert _kind("SELECT a FROM t") == "select"
    assert _kind("WITH c AS (SELECT 1 AS x) SELECT * FROM c") == "select"


def test_leading_comment_does_not_change_kind():
    assert _kind("-- explains it\nSELECT a FROM t") == "select"


def test_ctas_variants():
    assert _kind("CREATE TABLE x AS SELECT a FROM t") == "create_table_as"
    assert _kind("CREATE OR REPLACE TABLE x AS SELECT 1 AS a", "snowflake") == "create_table_as"
    assert _kind("CREATE TEMP TABLE x AS SELECT 1 AS a") == "create_table_as"
    assert _kind("SELECT a INTO #tmp FROM t", "tsql") == "create_table_as"


def test_bare_create_table_is_ddl_other():
    assert _kind("CREATE TABLE x (a INT)") == "ddl_other"


def test_create_view():
    assert _kind("CREATE OR REPLACE VIEW v AS SELECT a FROM t") == "create_view"


def test_insert_select_with_and_without_columns():
    assert _kind("INSERT INTO x SELECT a FROM t") == "insert_select"
    assert _kind("INSERT INTO x (a) SELECT a FROM t") == "insert_select"


def test_insert_values_is_unsupported_with_reason():
    stmt = classify(_raw("INSERT INTO x VALUES (1, 'a')"))
    assert stmt.kind == "unsupported"
    assert "VALUES" in stmt.reason


def test_merge():
    assert (
        _kind("MERGE INTO x USING y ON x.id = y.id WHEN MATCHED THEN UPDATE SET a = y.a") == "merge"
    )


def test_parse_error_is_unsupported_with_reason():
    stmt = classify(_raw("SELEC a FRM t"))
    assert stmt.kind == "unsupported"
    assert stmt.reason  # carries the parse error text


def test_reason_is_always_populated():
    for text in ["SELECT 1", "CREATE TABLE x AS SELECT 1 AS a", "SELEC nope"]:
        assert classify(_raw(text)).reason


def test_dml_kinds():
    assert _kind("DELETE FROM x WHERE a > 1") == "delete"
    assert _kind("UPDATE x SET a = 1 WHERE b = 2") == "update"
    assert _kind("TRUNCATE TABLE x") == "truncate"


def test_grant():
    assert _kind("GRANT SELECT ON x TO reporting_role") == "grant"


def test_variables():
    assert _kind("DECLARE @d DATE = '2024-01-01'", "tsql") == "variable"
    assert _kind("SET @x = 5", "tsql") == "variable"


def test_session_settings():
    assert _kind("SET search_path = analytics", "postgres") == "session"
    assert _kind("USE analytics", "tsql") == "session"
    assert _kind("ALTER SESSION SET QUERY_TAG = 'x'", "snowflake") == "session"


def test_ddl_other():
    assert _kind("CREATE INDEX ix ON x (a)") == "ddl_other"
    assert _kind("ALTER TABLE x ADD COLUMN b INT") == "ddl_other"
    assert _kind("DROP TABLE IF EXISTS x") == "ddl_other"


def test_procedural():
    assert _kind("CREATE PROCEDURE p AS BEGIN SELECT 1; END", "tsql") == "procedural"
    assert _kind("EXEC p", "tsql") == "procedural"


def test_command_fallback_is_unsupported():
    stmt = classify(_raw("PRINT 'hi'"), "tsql")
    assert stmt.kind == "unsupported"
    assert "could not parse this syntax" in stmt.reason
    assert "PRINT" in stmt.reason  # the raw text is carried in the reason


def test_copy_is_unsupported():
    stmt = classify(_raw("COPY INTO t FROM @stage"), "snowflake")
    assert stmt.kind == "unsupported"
    assert "COPY loads files" in stmt.reason


_TOTALITY_CASES: list[tuple[str, str | None, str]] = [
    ("SELECT a FROM t", None, "select"),
    ("CREATE TABLE x AS SELECT a FROM t", None, "create_table_as"),
    ("CREATE VIEW v AS SELECT a FROM t", None, "create_view"),
    ("INSERT INTO x SELECT a FROM t", None, "insert_select"),
    ("MERGE INTO x USING y ON x.id = y.id WHEN MATCHED THEN UPDATE SET a = y.a", None, "merge"),
    ("DELETE FROM x", None, "delete"),
    ("UPDATE x SET a = 1", None, "update"),
    ("TRUNCATE TABLE x", None, "truncate"),
    ("DECLARE @d INT = 1", "tsql", "variable"),
    ("USE analytics", "tsql", "session"),
    ("GRANT SELECT ON x TO r", None, "grant"),
    ("DROP TABLE x", None, "ddl_other"),
    ("EXEC p", "tsql", "procedural"),
    ("SELEC nope", None, "unsupported"),
]


@pytest.mark.parametrize(("sql", "dialect", "expected"), _TOTALITY_CASES)
def test_every_kind_is_reachable(sql, dialect, expected):
    assert _kind(sql, dialect) == expected


def test_totality_cases_cover_every_kind():
    import typing

    covered = {expected for _, _, expected in _TOTALITY_CASES}
    assert covered == set(typing.get_args(StatementKind))
