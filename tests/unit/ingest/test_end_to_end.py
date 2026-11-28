from pathlib import Path

from dbtw.core.ingest import classify_statements, ingest

FIXTURES = Path(__file__).parents[2] / "fixtures" / "sql"


def test_fixture_script_classifies_in_order():
    result = ingest(FIXTURES / "etl_script.sql", dialect="tsql")
    classified = classify_statements(result)
    assert [c.kind for c in classified] == [
        "session",  # USE analytics (with the file's header comment attached)
        "variable",  # DECLARE @start_date
        "truncate",  # TRUNCATE TABLE stg_daily_revenue
        "insert_select",  # INSERT INTO ... SELECT
        "create_table_as",  # SELECT ... INTO dim_customers
        "grant",  # GRANT SELECT
    ]
    assert all(c.reason for c in classified)
    assert classified[0].raw.text.startswith("-- Daily revenue ETL")


def test_classification_is_total_over_ingest():
    result = ingest(FIXTURES / "etl_script.sql", dialect="tsql")
    classified = classify_statements(result)
    assert len(classified) == len(result.statements)


def test_a_trailing_comment_is_not_classified_as_an_unsupported_statement(tmp_path):
    """The bug: the comment after the last statement came back as a
    statement, failed to parse, and showed as pending "unsupported" work."""
    (tmp_path / "load.sql").write_text(
        "INSERT INTO t SELECT a FROM s;\n-- end of the nightly load\n", encoding="utf-8"
    )

    classified = classify_statements(ingest(tmp_path, dialect="duckdb"))

    assert [c.kind for c in classified] == ["insert_select"]
