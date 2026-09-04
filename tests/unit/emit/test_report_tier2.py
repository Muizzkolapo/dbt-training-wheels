from pathlib import Path

import yaml

from dbtw.core.assemble import AssembledModel, ProjectChange, SourceEntry, Variable
from dbtw.core.context import read_project
from dbtw.core.emit.report import render_report
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
        ),
        pending=(),
        dialect="tsql",
        project_name="jaffle_shop",
        variables=(),
    )
    base.update(kw)
    return ProjectChange(**base)  # type: ignore[arg-type]


def _report(**kw) -> str:
    return render_report(_change(**kw), read_project(FIXTURES / "jaffle_shop"))


def test_vars_section_appears_with_yaml_block_and_merge_warning():
    out = _report(
        variables=(
            Variable(
                name="cutoff",
                default_sql="'2024-01-01'",
                source_file="etl.sql",
                line_start=2,
            ),
        )
    )
    assert "## Add to your dbt_project.yml" in out
    assert "```yaml" in out
    assert "vars:" in out
    assert "  cutoff: '2024-01-01'" in out
    # the merge warning: fragment to merge, not a replacement
    assert "merge" in out.lower()
    assert "dbt_project.yml" in out
    # placed immediately after ## Sources
    headings = [line for line in out.splitlines() if line.startswith("## ")]
    sources_idx = headings.index("## Sources")
    assert headings[sources_idx + 1] == "## Add to your dbt_project.yml"


def test_vars_section_absent_when_no_variables():
    out = _report(variables=())
    assert "## Add to your dbt_project.yml" not in out


def test_no_default_variable_renders_placeholder_comment():
    out = _report(
        variables=(
            Variable(
                name="region",
                default_sql=None,
                source_file="etl.sql",
                line_start=5,
            ),
        )
    )
    assert "  region:  # no default in the source; set one" in out


def test_question_bearing_decision_renders_question_chosen_and_alternatives():
    out = _report(
        decisions=(
            Decision(
                key="k2",
                tier=2,
                action="chose incremental strategy",
                reason="Tier 2 owns it",
                source_file="etl.sql",
                line_start=9,
                line_end=9,
                question="How should late-arriving rows be handled?",
                chosen="merge",
                alternatives=("append", "full refresh"),
            ),
        )
    )
    assert "chose incremental strategy" in out
    assert "Question: How should late-arriving rows be handled?" in out
    assert "Chose: merge" in out
    assert "(alternatives: append, full refresh)" in out


def test_decision_without_question_renders_as_before():
    out = _report()
    assert "Question:" not in out
    assert "created model stg_orders" in out


def test_old_reference_sentence_is_gone_and_new_sentence_names_incrementals():
    out = _report()
    assert "Table references are not yet rewritten as ref() or source() calls." not in out
    assert "incremental" in out.lower()
    assert "rewritten" in out.lower()


def test_vars_block_stays_parseable_yaml_for_awkward_defaults():
    out = _report(
        variables=(
            Variable(
                name="window",
                default_sql="'12:00:00'",
                source_file="etl.sql",
                line_start=1,
            ),
            Variable(
                name="owner",
                default_sql="'O''Brien'",
                source_file="etl.sql",
                line_start=2,
            ),
        )
    )
    block = out.split("```yaml\n", 1)[1].split("```", 1)[0]
    loaded = yaml.safe_load(block)
    assert loaded["vars"]["window"] == "12:00:00"
    assert loaded["vars"]["owner"] == "O'Brien"


def test_question_bearing_decision_with_no_alternatives_omits_the_parenthetical():
    out = _report(
        decisions=(
            Decision(
                key="k2",
                tier=2,
                action="chose incremental strategy",
                reason="Tier 2 owns it",
                source_file="etl.sql",
                line_start=9,
                line_end=9,
                question="How should late-arriving rows be handled?",
                chosen="merge",
                alternatives=(),
            ),
        )
    )
    assert "Chose: merge" in out
    assert "(alternatives:" not in out
