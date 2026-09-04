from pathlib import Path

import pytest

from dbtw.cli.main import main

ROOT = Path(__file__).parents[2] / "fixtures"
SQL = ROOT / "sql" / "etl_script.sql"
PROJECT = ROOT / "projects" / "jaffle_shop"


def _run(tmp_path, *extra: str) -> int:
    return main(["convert", str(SQL), "--project", str(PROJECT), "--out", str(tmp_path), *extra])


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


def test_report_records_the_pending_variable_and_the_dropped_use(tmp_path):
    _run(tmp_path, "--dialect", "tsql")
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "variable" in report  # the DECLARE is still pending
    assert "profiles.yml" in report  # the USE was dropped with its reason
    assert "Table references are not yet rewritten" in report


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
