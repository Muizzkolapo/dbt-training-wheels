from dbtw.core.ingest import RawStatement
from dbtw.core.ingest.classifier import classify


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
