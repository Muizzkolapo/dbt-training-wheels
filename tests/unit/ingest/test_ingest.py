from pathlib import Path

import pytest

from dbtw.core.ingest import UnknownDialectError, ingest

FIXTURES = Path(__file__).parents[2] / "fixtures" / "sql"


def test_ingests_fixture_file_into_six_statements():
    result = ingest(FIXTURES / "etl_script.sql", dialect="tsql")
    assert len(result.statements) == 6
    assert result.dialect == "tsql"
    first = result.statements[0]
    assert first.index == 0
    assert first.text.startswith("-- Daily revenue ETL")
    assert first.text.endswith("USE analytics")
    assert [s.index for s in result.statements] == [0, 1, 2, 3, 4, 5]


def test_directory_walks_sql_files(tmp_path):
    (tmp_path / "b.sql").write_text("SELECT 2")
    (tmp_path / "a.sql").write_text("SELECT 1")
    (tmp_path / "notes.txt").write_text("not sql")
    result = ingest(tmp_path, dialect="duckdb")
    assert [s.source_file for s in result.statements] == [
        str(tmp_path / "a.sql"),
        str(tmp_path / "b.sql"),
    ]


def test_unknown_dialect_raises_with_valid_names():
    with pytest.raises(UnknownDialectError, match="tsql"):
        ingest(FIXTURES / "etl_script.sql", dialect="sqlserver")


def test_missing_path_raises():
    with pytest.raises(FileNotFoundError):
        ingest(FIXTURES / "does_not_exist.sql")


def test_no_dialect_is_a_recorded_warning(tmp_path):
    (tmp_path / "q.sql").write_text("SELECT 1")
    result = ingest(tmp_path)
    assert result.dialect is None
    assert any("no dialect specified" in w for w in result.warnings)


def test_non_utf8_file_skipped_with_warning(tmp_path):
    (tmp_path / "bad.sql").write_bytes(b"SELECT 'caf\xe9'")
    (tmp_path / "good.sql").write_text("SELECT 1")
    result = ingest(tmp_path, dialect="duckdb")
    assert len(result.statements) == 1
    assert any("bad.sql" in w for w in result.warnings)
