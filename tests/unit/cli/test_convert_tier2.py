from pathlib import Path

from dbtw.cli.main import main

ROOT = Path(__file__).parents[2] / "fixtures"
PROJECT = ROOT / "projects" / "jaffle_shop"


def _run(tmp_path, sql, *extra):
    return main(
        [
            "convert",
            str(ROOT / "sql" / sql),
            "--project",
            str(PROJECT),
            "--out",
            str(tmp_path),
            *extra,
        ]
    )


def test_qualified_script_produces_idiomatic_dbt(tmp_path):
    assert _run(tmp_path, "qualified_etl.sql") == 0
    staging = (tmp_path / "models" / "staging" / "stg_order_totals.sql").read_text()
    assert "{{ source('raw', 'orders') }} AS o" in staging
    mart = (tmp_path / "models" / "daily_rollup.sql").read_text()
    assert "{{ ref('stg_order_totals') }}" in mart


def test_sources_yml_declares_the_source_that_was_referenced(tmp_path):
    _run(tmp_path, "qualified_etl.sql")
    sources = (tmp_path / "models" / "staging" / "sources.yml").read_text()
    assert "name: raw" in sources and "name: orders" in sources


def test_variables_become_vars_and_the_report_says_what_to_add(tmp_path):
    assert _run(tmp_path, "etl_script.sql", "--dialect", "tsql") == 0
    body = (tmp_path / "models" / "staging" / "stg_daily_revenue.sql").read_text()
    assert "{{ var('start_date') }}" in body
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "Add to your dbt_project.yml" in report
    assert "start_date" in report
    assert "Nothing — every statement was handled." in report  # the DECLARE is no longer pending


def test_inline_vars_flag_substitutes_the_literal(tmp_path):
    assert _run(tmp_path, "etl_script.sql", "--dialect", "tsql", "--inline-vars") == 0
    body = (tmp_path / "models" / "staging" / "stg_daily_revenue.sql").read_text()
    assert "'2024-01-01'" in body
    assert "var(" not in body
