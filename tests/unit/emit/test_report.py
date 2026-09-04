from pathlib import Path

from dbtw.core.assemble import AssembledModel, ProjectChange, SourceEntry
from dbtw.core.context import read_project
from dbtw.core.emit.report import render_report
from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import Decision

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _change(**kw) -> ProjectChange:
    base = dict(
        models=(
            AssembledModel(
                name="stg_orders",
                path="models/staging/stg_orders.sql",
                body="SELECT 1 AS a",
                materialization=None,
                grants=(),
                layer="staging",
                depends_on=(),
                leading_comments=(),
                source_indices=(0,),
            ),
        ),
        sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),),
        decisions=(
            Decision(
                key="k1",
                tier=1,
                action="created model stg_orders",
                reason="because dbt",
                source_file="etl.sql",
                line_start=3,
                line_end=4,
            ),
            Decision(
                key="k2",
                tier=2,
                action="deferred something",
                reason="Tier 2 owns it",
                source_file="etl.sql",
                line_start=9,
                line_end=9,
            ),
        ),
        pending=(),
        dialect="tsql",
        project_name="jaffle_shop",
    )
    base.update(kw)
    return ProjectChange(**base)  # type: ignore[arg-type]


def _report(**kw) -> str:
    return render_report(_change(**kw), read_project(FIXTURES / "jaffle_shop"))


def test_all_sections_present_in_order():
    out = _report()
    headings = [line for line in out.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Summary",
        "## Your project's conventions",
        "## Models",
        "## Sources",
        "## Decisions",
        "## Still pending",
        "## Not done yet",
    ]


def test_summary_counts_and_dialect():
    out = _report()
    assert "jaffle_shop" in out
    assert "tsql" in out


def test_conventions_section_quotes_detection_evidence():
    out = _report()
    assert "layer.staging.prefix" in out
    assert "models/staging" in out  # the evidence string


def test_models_table_shows_layer_default_materialization():
    out = _report()
    assert "(layer default)" in out


def test_decisions_are_grouped_by_tier_with_locations():
    out = _report()
    assert "created model stg_orders" in out
    assert "etl.sql:3" in out
    assert "deferred something" in out


def test_pending_statements_are_listed():
    raw = RawStatement(
        source_file="etl.sql", index=7, text="DECLARE @d INT = 1", line_start=9, line_end=9
    )
    stmt = ClassifiedStatement(raw=raw, kind="variable", reason="test")
    out = _report(pending=((7, stmt),))
    assert "variable" in out
    assert "DECLARE @d INT = 1" in out


def test_empty_pending_says_so():
    assert "Nothing — every statement was handled." in _report()


def test_not_done_yet_names_the_reference_gap():
    assert "Table references are not yet rewritten as ref() or source() calls." in _report()


def test_no_sources_says_none_to_declare():
    assert "None to declare." in _report(sources=())
