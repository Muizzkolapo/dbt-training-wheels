from pathlib import Path

from dbtw.core.ingest import classify_statements, ingest
from dbtw.core.ingest.classifier import classify
from dbtw.core.ingest.types import RawStatement
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


def test_query_less_create_view_survives_the_full_pipeline():
    # Regression for the crash: "CREATE VIEW v" (no AS query) used to
    # classify as create_view, then build_models_pass raised AttributeError
    # calling .sql() on a None expression. It must classify as ddl_other and
    # be dropped by drop_ddl_pass with a Decision, not raise.
    raw = RawStatement(source_file="t.sql", index=0, text="CREATE VIEW v", line_start=1, line_end=1)
    classified = classify(raw)
    assert classified.kind == "ddl_other"

    out = run_passes((classified,), dialect=None)
    assert out.pending == ()
    (dec,) = out.decisions
    assert dec.tier == 1
    assert dec.reason
