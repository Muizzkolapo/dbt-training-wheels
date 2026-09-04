from pathlib import Path

import yaml

from dbtw.core.assemble import assemble
from dbtw.core.context import read_project
from dbtw.core.emit.report import render_report
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


def _set_variable_state():
    """FINDING 3 probe (duckdb): SET VARIABLE declares `cutoff`; the body
    reads it back via GETVARIABLE('cutoff') — the real duckdb reference
    syntax, not a Parameter. Before the fix, the report claimed cutoff was
    "declared as a dbt var, referenced via var('cutoff')" while the body
    still called GETVARIABLE('cutoff'), which returns NULL at run time.
    """
    raw = RawStatement(
        source_file="e.sql",
        index=0,
        text="SET VARIABLE cutoff = '2024-06-30'",
        line_start=1,
        line_end=1,
    )
    stmt = ClassifiedStatement(raw=raw, kind="variable", reason="t")
    draft = ModelDraft(
        name="m",
        qualified_name="m",
        body="SELECT a FROM raw.t WHERE d >= GETVARIABLE('cutoff')",
        materialization="table",
        grants=(),
        source_indices=(1,),
        leading_comments=(),
    )
    return PassState(pending=((0, stmt),), drafts=(draft,), decisions=(), dialect="duckdb")


def test_set_variable_reference_is_actually_rewritten_to_a_var_call():
    change = assemble(_set_variable_state(), read_project(PROJECTS / "jaffle_shop"))
    body = change.models[0].body
    assert "{{ var('cutoff') }}" in body
    assert "GETVARIABLE" not in body.upper()
    assert change.pending == ()
    assert [v.name for v in change.variables] == ["cutoff"]


def _spark_set_var_state():
    """FINDING 3 correction probe (spark): SET VAR declares `cutoff`; the
    body reads it back via a BARE IDENTIFIER (`cutoff`) — Spark/Databricks'
    real read-back form, since Spark has no GETVARIABLE function at all.
    The statement must be left pending, with a Decision explaining why, and
    the body must be left completely untouched: a bare `cutoff` is
    indistinguishable from a column and must never be rewritten.
    """
    raw = RawStatement(
        source_file="e.sql",
        index=0,
        text="SET VAR cutoff = '2024-06-30'",
        line_start=1,
        line_end=1,
    )
    stmt = ClassifiedStatement(raw=raw, kind="variable", reason="t")
    draft = ModelDraft(
        name="m",
        qualified_name="m",
        body="SELECT a FROM raw.t WHERE d >= cutoff",
        materialization="table",
        grants=(),
        source_indices=(1,),
        leading_comments=(),
    )
    return PassState(pending=((0, stmt),), drafts=(draft,), decisions=(), dialect="spark")


def test_spark_set_var_is_left_pending_with_an_explaining_decision():
    change = assemble(_spark_set_var_state(), read_project(PROJECTS / "jaffle_shop"))
    body = change.models[0].body
    assert "var(" not in body
    assert "cutoff" in body
    assert len(change.pending) == 1
    assert change.pending[0][1].raw.text == "SET VAR cutoff = '2024-06-30'"
    assert change.variables == ()
    deferred = [d for d in change.decisions if "bare identifier" in d.reason.lower()]
    assert len(deferred) == 1


def _declare_then_set_state():
    """FINDING 4 probe: DECLARE @cutoff DATE; SET @cutoff = '2024-06-30'; —
    the DECLARE's None default (first-wins dedup) previously survived into
    the report even though the very next statement set a real value, and
    both statements were consumed. Extraction preserves statement order, so
    the later SET's non-None default must fill the DECLARE's recorded None.
    """
    declare_raw = RawStatement(
        source_file="e.sql", index=0, text="DECLARE @cutoff DATE", line_start=1, line_end=1
    )
    set_raw = RawStatement(
        source_file="e.sql",
        index=1,
        text="SET @cutoff = '2024-06-30'",
        line_start=2,
        line_end=2,
    )
    pending = (
        (0, ClassifiedStatement(raw=declare_raw, kind="variable", reason="t")),
        (1, ClassifiedStatement(raw=set_raw, kind="variable", reason="t")),
    )
    draft = ModelDraft(
        name="m",
        qualified_name="m",
        body="SELECT a FROM raw.t WHERE d >= @cutoff",
        materialization="table",
        grants=(),
        source_indices=(2,),
        leading_comments=(),
    )
    return PassState(pending=pending, drafts=(draft,), decisions=(), dialect="tsql")


def test_declare_then_set_reports_the_assigned_value_not_none():
    change = assemble(_declare_then_set_state(), read_project(PROJECTS / "jaffle_shop"))
    assert change.pending == ()  # both statements consumed
    assert [(v.name, v.default_sql) for v in change.variables] == [("cutoff", "'2024-06-30'")]


def test_declare_then_set_inlines_the_assigned_value():
    change = assemble(
        _declare_then_set_state(), read_project(PROJECTS / "jaffle_shop"), inline_vars=True
    )
    assert "'2024-06-30'" in change.models[0].body
    assert "var(" not in change.models[0].body


def _default_less_variable_state():
    """FINDING 5 probe: --inline-vars on a variable with no default in the
    source. The assembler took the inline branch unconditionally on
    inline_vars, without checking default_sql is not None — the Decision
    claimed "inlined region's literal default value", the body actually
    fell back to var('region') (rewrite.py correctly refuses to inline
    nothing), and the var was never added to change.variables — so the vars
    section is absent from the report AND `dbt compile` fails on an
    undefined var. The report lied and the output was broken.
    """
    raw = RawStatement(
        source_file="e.sql", index=0, text="DECLARE @region VARCHAR(50)", line_start=1, line_end=1
    )
    stmt = ClassifiedStatement(raw=raw, kind="variable", reason="t")
    draft = ModelDraft(
        name="m",
        qualified_name="m",
        body="SELECT a FROM raw.t WHERE r = @region",
        materialization="table",
        grants=(),
        source_indices=(1,),
        leading_comments=(),
    )
    return PassState(pending=((0, stmt),), drafts=(draft,), decisions=(), dialect="tsql")


def test_inline_vars_on_a_default_less_variable_keeps_it_as_a_var_honestly():
    change = assemble(
        _default_less_variable_state(), read_project(PROJECTS / "jaffle_shop"), inline_vars=True
    )
    assert "{{ var('region') }}" in change.models[0].body
    assert [v.name for v in change.variables] == ["region"]
    var_decisions = [d for d in change.decisions if d.key == "assemble.variable.region"]
    assert len(var_decisions) == 1
    action = var_decisions[0].action.lower()
    assert "inlined" not in action
    assert "no" in action and ("default" in action or "literal" in action)


def _target_declared_variable_state():
    """FINDING 6 probe: the `with_sources` fixture project already declares
    `start_date` in its own dbt_project.yml. The script DECLAREs start_date
    again locally, with a different literal, and --inline-vars is on. The
    Decision says "its reference was rewritten to var(), not re-declared" —
    but variable_defaults[name] was populated with the script's own local
    default before the declared-in-target `continue`, so rewrite_body still
    inlined that local literal, silently overriding the project's own
    declared value. Decision and disk disagreed.
    """
    raw = RawStatement(
        source_file="e.sql",
        index=0,
        text="DECLARE @start_date DATE = '1999-01-01'",
        line_start=1,
        line_end=1,
    )
    stmt = ClassifiedStatement(raw=raw, kind="variable", reason="t")
    draft = ModelDraft(
        name="m",
        qualified_name="m",
        body="SELECT a FROM raw.t WHERE d >= @start_date",
        materialization="table",
        grants=(),
        source_indices=(1,),
        leading_comments=(),
    )
    return PassState(pending=((0, stmt),), drafts=(draft,), decisions=(), dialect="tsql")


def test_target_declared_variable_is_never_inlined_even_with_inline_vars():
    change = assemble(
        _target_declared_variable_state(),
        read_project(PROJECTS / "with_sources"),
        inline_vars=True,
    )
    body = change.models[0].body
    assert "{{ var('start_date') }}" in body
    assert "1999-01-01" not in body
    decision = next(d for d in change.decisions if d.key == "assemble.variable.start_date")
    assert "var(" in decision.action
    assert "not re-declared" in decision.action


def _compound_default_state():
    raw = RawStatement(
        source_file="e.sql", index=0, text="DECLARE @n INT = 1 + 2", line_start=1, line_end=1
    )
    stmt = ClassifiedStatement(raw=raw, kind="variable", reason="t")
    draft = ModelDraft(
        name="m",
        qualified_name="m",
        body="SELECT @n * 3 AS result",
        materialization="table",
        grants=(),
        source_indices=(1,),
        leading_comments=(),
    )
    return PassState(pending=((0, stmt),), drafts=(draft,), decisions=(), dialect="tsql")


def _squashed(sql: str) -> str:
    return sql.replace(" ", "").replace("\n", "")


def test_var_path_and_inline_path_agree_on_a_compound_default():
    """FINDING 7 (both paths must agree): DECLARE @n INT = 1 + 2, read as
    @n * 3. --inline-vars produces (1 + 2) * 3 = 9 (FINDING 7's first fix);
    the var()-kept path's YAML fragment must describe the exact same
    expression, or a user who reads the report's vars block and pastes it
    into dbt_project.yml gets a DIFFERENT answer (1 + 2 * 3 = 7) than a user
    who ran --inline-vars on the identical script.
    """
    ctx = read_project(PROJECTS / "jaffle_shop")

    kept = assemble(_compound_default_state(), ctx)
    report = render_report(kept, ctx)
    vars_block = report.split("```yaml\n", 1)[1].split("```", 1)[0]
    loaded = yaml.safe_load(vars_block)
    assert loaded["vars"]["n"] == "(1 + 2)"

    inlined = assemble(_compound_default_state(), ctx, inline_vars=True)
    assert "(1+2)*3" in _squashed(inlined.models[0].body)

    # Both paths' effective SQL must literally be the same parenthesized
    # expression — not just "both happen to evaluate to 9".
    assert loaded["vars"]["n"].replace(" ", "") in _squashed(inlined.models[0].body)
