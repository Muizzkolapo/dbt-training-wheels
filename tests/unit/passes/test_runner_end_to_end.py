from pathlib import Path

from dbtw.core.ingest import classify_statements, ingest
from dbtw.core.passes import run_passes

FIXTURES = Path(__file__).parents[2] / "fixtures" / "sql"


def test_tier1_pipeline_over_the_etl_fixture():
    """The fixture: USE; DECLARE; TRUNCATE stg_daily_revenue; INSERT..SELECT;
    SELECT INTO dim_customers; GRANT SELECT ON dim_customers."""
    classified = classify_statements(ingest(FIXTURES / "etl_script.sql", dialect="tsql"))
    out = run_passes(classified, dialect="tsql")

    drafts = {d.name: d for d in out.drafts}
    assert set(drafts) == {"stg_daily_revenue", "dim_customers"}
    assert drafts["stg_daily_revenue"].materialization == "table"
    assert drafts["stg_daily_revenue"].source_indices == (2, 3)
    assert "raw_orders" in drafts["stg_daily_revenue"].body
    assert drafts["dim_customers"].materialization == "table"
    assert drafts["dim_customers"].grants == (("SELECT", ("reporting",)),)

    # only the DECLARE (a Tier-2 variable) remains for later passes
    assert [stmt.kind for _, stmt in out.pending] == ["variable"]

    # every consumed statement left a decision; all tier 1; all have reasons
    assert len(out.decisions) == 4  # pair, build, grant, session-drop
    assert all(d.tier == 1 for d in out.decisions)
    assert all(d.reason for d in out.decisions)


def test_empty_input_is_empty_output():
    out = run_passes((), dialect=None)
    assert out.pending == () and out.drafts == () and out.decisions == ()
