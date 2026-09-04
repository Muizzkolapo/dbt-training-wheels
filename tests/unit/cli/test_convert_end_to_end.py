from pathlib import Path

import pytest

from dbtw.cli.main import main

ROOT = Path(__file__).parents[2] / "fixtures"
SQL = ROOT / "sql" / "etl_script.sql"
PROJECT = ROOT / "projects" / "jaffle_shop"


def _run(tmp_path, *extra: str) -> int:
    return main(["convert", str(SQL), "--project", str(PROJECT), "--out", str(tmp_path), *extra])


def _run_against(sql_path, out_dir, *extra: str) -> int:
    return main(
        ["convert", str(sql_path), "--project", str(PROJECT), "--out", str(out_dir), *extra]
    )


def test_convert_writes_models_and_a_report(tmp_path, capsys):
    assert _run(tmp_path, "--dialect", "tsql") == 0
    report = tmp_path / "CONVERSION_REPORT.md"
    assert report.is_file()
    written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.sql")}
    assert "models/staging/stg_daily_revenue.sql" in written
    assert "models/staging/stg_dim_customers.sql" in written
    out = capsys.readouterr().out
    assert "2 models" in out
    assert "CONVERSION_REPORT.md" in out


def test_converted_model_content_is_dbt_shaped(tmp_path):
    _run(tmp_path, "--dialect", "tsql")
    revenue = (tmp_path / "models" / "staging" / "stg_daily_revenue.sql").read_text()
    assert revenue.startswith(
        "{{ config(\n    materialized='table'\n) }}"
    )  # staging default is view
    assert "raw_orders" in revenue
    assert "TRUNCATE" not in revenue.upper()
    customers = (tmp_path / "models" / "staging" / "stg_dim_customers.sql").read_text()
    assert "grants={'select': ['reporting']}" in customers
    assert "INTO" not in customers.upper()


def test_report_records_the_variable_as_a_var_and_the_dropped_use(tmp_path):
    _run(tmp_path, "--dialect", "tsql")
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "start_date" in report  # the DECLARE was extracted and rewritten to a dbt var
    assert "Nothing — every statement was handled." in report  # no longer pending
    assert "profiles.yml" in report  # the USE was dropped with its reason
    assert "Table references and script variables have been rewritten" in report


def test_unknown_dialect_exits_two(tmp_path, capsys):
    assert _run(tmp_path, "--dialect", "sqlserver") == 2
    assert "sqlserver" in capsys.readouterr().err


def test_missing_project_exits_two(tmp_path, capsys):
    code = main(["convert", str(SQL), "--project", str(tmp_path / "nope"), "--out", str(tmp_path)])
    assert code == 2
    assert capsys.readouterr().err


def test_no_arguments_exits_two(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_ingest_warnings_are_printed_to_stderr(tmp_path, capsys):
    """A non-UTF-8 .sql file is skipped by ingest() and recorded as a warning
    (ingestor.py). The CLI must surface it — silently dropping an entire
    input file while still exiting 0 is exactly the kind of untruthful
    report the project's reporting rule forbids.
    """
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    (sql_dir / "good.sql").write_text("SELECT 1 AS a", encoding="utf-8")
    bad_file = sql_dir / "bad.sql"
    bad_file.write_bytes(b"SELECT 1 AS a -- \xff\xfe not valid utf-8 \x80")
    out_dir = tmp_path / "out"

    code = _run_against(sql_dir, out_dir, "--dialect", "tsql")
    assert code == 0
    err = capsys.readouterr().err
    assert "warning:" in err
    assert "bad.sql" in err


def test_out_path_pointing_at_an_existing_file_exits_two_not_a_traceback(tmp_path, capsys):
    out_as_file = tmp_path / "out"
    out_as_file.write_text("occupied", encoding="utf-8")
    code = _run_against(SQL, out_as_file, "--dialect", "tsql")
    assert code == 2
    assert capsys.readouterr().err


def test_a_quoted_identifier_that_escapes_out_dir_exits_two_not_a_traceback(tmp_path, capsys):
    """A quoted identifier like "../../deep_escape" survives ingestion and
    naming untouched, and only trips emit's out-of-out_dir guard at write
    time. That guard is correct to refuse the write — but the refusal is
    input-driven (a quoted identifier in the source SQL), not a dbtw bug,
    and must exit 2 like every other bad-input case, not crash with a
    traceback.
    """
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    (sql_dir / "escape.sql").write_text(
        "CREATE TABLE base_t AS SELECT 1 AS a;\n"
        'CREATE TABLE "../../deep_escape" AS SELECT a FROM base_t;\n',
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    code = _run_against(sql_dir, out_dir, "--dialect", "tsql")
    assert code == 2
    assert capsys.readouterr().err
