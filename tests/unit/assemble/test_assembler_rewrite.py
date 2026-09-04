from pathlib import Path

from dbtw.core.assemble import assemble
from dbtw.core.context import read_project
from dbtw.core.ingest import ClassifiedStatement, RawStatement, classify_statements, ingest
from dbtw.core.passes import ModelDraft, PassState, run_passes

FIXTURES = Path(__file__).parents[2] / "fixtures"
PROJECTS = FIXTURES / "projects"


def _change(project="jaffle_shop", inline_vars=False):
    classified = classify_statements(ingest(FIXTURES / "sql" / "qualified_etl.sql", dialect=None))
    state = run_passes(classified, dialect=None)
    return assemble(state, read_project(PROJECTS / project), inline_vars=inline_vars)


def _by_name(change):
    return {m.name: m for m in change.models}


def test_qualified_source_reference_is_rewritten():
    models = _by_name(_change())
    assert "{{ source('raw', 'orders') }}" in models["stg_order_totals"].body


def test_alias_survives_in_the_emitted_body():
    body = _by_name(_change())["stg_order_totals"].body
    assert "AS o" in body and "o.id" in body


def test_model_to_model_reference_uses_the_final_name():
    body = _by_name(_change())["daily_rollup"].body
    assert "{{ ref('stg_order_totals') }}" in body
    assert "FROM order_totals" not in body


def test_every_rewrite_is_recorded_as_a_tier_two_decision():
    change = _change()
    rewrites = [d for d in change.decisions if d.tier == 2]
    assert rewrites
    assert all(d.reason for d in rewrites)


def test_unresolved_reference_is_left_as_written_with_a_decision():
    draft = ModelDraft(
        name="m",
        qualified_name="m",
        body="SELECT a FROM mystery_table",
        materialization="table",
        grants=(),
        source_indices=(0,),
        leading_comments=(),
    )
    change = assemble(
        PassState(pending=(), drafts=(draft,), decisions=(), dialect=None),
        read_project(PROJECTS / "jaffle_shop"),
    )
    assert "mystery_table" in change.models[0].body
    assert "{{" not in change.models[0].body
    assert any("left as written" in d.action for d in change.decisions)


def _variable_state():
    raw = RawStatement(
        source_file="e.sql",
        index=0,
        text="DECLARE @cutoff DATE = '2024-01-01'",
        line_start=1,
        line_end=1,
    )
    stmt = ClassifiedStatement(raw=raw, kind="variable", reason="t")
    draft = ModelDraft(
        name="m",
        qualified_name="m",
        body="SELECT a FROM raw.t WHERE d >= @cutoff",
        materialization="table",
        grants=(),
        source_indices=(1,),
        leading_comments=(),
    )
    return PassState(pending=((0, stmt),), drafts=(draft,), decisions=(), dialect="tsql")


def test_variable_becomes_a_var_and_leaves_pending():
    change = assemble(_variable_state(), read_project(PROJECTS / "jaffle_shop"))
    assert "{{ var('cutoff') }}" in change.models[0].body
    assert change.pending == ()
    assert [v.name for v in change.variables] == ["cutoff"]
    q = [d for d in change.decisions if d.question]
    assert q and "cutoff" in q[0].question
    assert q[0].chosen == "keep as a dbt var"
    assert q[0].alternatives


def test_inline_vars_substitutes_the_literal_and_declares_nothing():
    change = assemble(_variable_state(), read_project(PROJECTS / "jaffle_shop"), inline_vars=True)
    assert "'2024-01-01'" in change.models[0].body
    assert "var(" not in change.models[0].body
    assert change.variables == ()
