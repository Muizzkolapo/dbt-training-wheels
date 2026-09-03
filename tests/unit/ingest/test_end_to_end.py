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
