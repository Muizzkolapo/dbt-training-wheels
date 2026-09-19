from pathlib import Path

import yaml

from dbtw.core.assemble import AssembledModel, ProjectChange, SourceEntry, Variable
from dbtw.core.context import read_project
from dbtw.core.emit.report import render_report
from dbtw.core.passes import (
    Decision,
    Option,
    append_option,
    inline_option,
    merge_option,
    var_option,
)

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
    # Double-quoted, wrapping the SQL literal (with its own single quotes)
    # verbatim — a bare single-quoted `cutoff: '2024-01-01'` is a YAML
    # single-quoted scalar and loses its quotes on load (FINDING 1).
    assert "  cutoff: \"'2024-01-01'\"" in out
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


def test_question_bearing_decision_renders_question_chosen_and_every_effect():
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
                # Three real answers under wording no pass produces, which is
                # what lets this assert that the renderer prints the strings
                # it is given verbatim rather than any wording it knows: no
                # label or effect below is one a factory in `passes/types.py`
                # builds. They are consistent triples, not placeholders --
                # with `Option.kind` a closed set over the answers this tool
                # offers, an Option standing for a non-answer is no longer
                # something the record can express, so a made-up option is
                # made up in its wording, not in its identity.
                options=(
                    Option(
                        label="merge",
                        kind="merge",
                        effect="updates matching rows and inserts the rest",
                    ),
                    Option(label="append", kind="append", effect="re-inserts everything selected"),
                    Option(
                        label="merge, and check the key",
                        kind="merge_checked",
                        effect="the same merge, and dbt checks the chosen column",
                        # Set, not defaulted: this option says in its effect
                        # that dbt checks the column, and `declares_test` is
                        # the field that makes that true. Leaving it "" would
                        # be a record contradicting its own text, which is the
                        # thing this file's renderer exists to never do -- and
                        # the invariant test over the factories cannot reach a
                        # hand-built option like this one.
                        declares_test="unique",
                    ),
                ),
            ),
        )
    )
    assert "chose incremental strategy" in out
    assert "Question: How should late-arriving rows be handled?" in out
    assert "Chose: merge" in out
    # Every option, with the effect the engine wrote for it: the report and
    # the screen render from the same records and must not explain a choice
    # differently, which labels alone cannot do. Each line names one option
    # and its whole effect, so dropping either fails here.
    assert "    - merge (chosen) — updates matching rows and inserts the rest" in out
    assert "    - append — re-inserts everything selected" in out
    checked = "    - merge, and check the key — the same merge, and dbt checks the chosen column"
    assert checked in out
    # Only the option that stands is marked as taken.
    assert out.count("(chosen)") == 1


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
    # The loaded value must be the SQL literal TEXT, quotes and all — that's
    # what var() renders byte-identically to what --inline-vars would splice
    # into the SQL. If YAML strips the quotes (loaded["window"] == "12:00:00"
    # instead of "'12:00:00'"), a date/string default renders unquoted into
    # the compiled SQL and becomes integer arithmetic instead of a literal
    # (see FINDING 1: `WHERE order_date >= 2024-01-01` compiles as date minus
    # 1 minus 1, not a date comparison).
    assert loaded["vars"]["window"] == "'12:00:00'"
    assert loaded["vars"]["owner"] == "'O''Brien'"


def test_vars_block_escapes_a_default_containing_a_double_quote():
    """A SQL literal that itself contains a double-quote character (e.g. an
    apostrophe-quoted string holding one) must not break the double-quoted
    YAML scalar it's wrapped in — otherwise the fix for awkward single-quote
    defaults just trades one unparseable-YAML failure mode for another.
    """
    out = _report(
        variables=(
            Variable(
                name="quip",
                default_sql="'she said \"hi\"'",
                source_file="etl.sql",
                line_start=1,
            ),
        )
    )
    block = out.split("```yaml\n", 1)[1].split("```", 1)[0]
    loaded = yaml.safe_load(block)
    assert loaded["vars"]["quip"] == "'she said \"hi\"'"


def test_compound_default_is_parenthesized_in_the_vars_block():
    """FINDING 7 (var() path — the default, more common path): a compound
    default (`1 + 2`) must reach var() with the same defensive parens
    --inline-vars gives it, or `{{ var('n') }} * 3` (from this YAML value)
    and an inlined `(1 + 2) * 3` compute different things for the same
    variable. Reuses naming.is_atomic_sql/maybe_paren — the same rule
    rewrite.py's --inline-vars path already applies.
    """
    out = _report(
        variables=(
            Variable(
                name="n",
                default_sql="1 + 2",
                source_file="etl.sql",
                line_start=1,
            ),
        )
    )
    block = out.split("```yaml\n", 1)[1].split("```", 1)[0]
    loaded = yaml.safe_load(block)
    assert loaded["vars"]["n"] == "(1 + 2)"


def test_atomic_default_is_not_parenthesized_in_the_vars_block():
    out = _report(
        variables=(
            Variable(
                name="cutoff",
                default_sql="'2024-01-01'",
                source_file="etl.sql",
                line_start=1,
            ),
        )
    )
    block = out.split("```yaml\n", 1)[1].split("```", 1)[0]
    loaded = yaml.safe_load(block)
    assert loaded["vars"]["cutoff"] == "'2024-01-01'"


def test_already_parenthesized_default_is_not_double_wrapped_in_the_vars_block():
    out = _report(
        variables=(
            Variable(
                name="n",
                default_sql="(1 + 2)",
                source_file="etl.sql",
                line_start=1,
            ),
        )
    )
    block = out.split("```yaml\n", 1)[1].split("```", 1)[0]
    loaded = yaml.safe_load(block)
    assert loaded["vars"]["n"] == "(1 + 2)"


def test_vars_block_falls_back_to_unwrapped_when_the_default_will_not_parse():
    """Defensive: an unparseable default_sql — which extraction should never
    actually produce, since it always comes from a node that did parse —
    must not crash report rendering.
    """
    out = _report(
        variables=(
            Variable(
                name="n",
                default_sql="SELEC garbage NOPE (",
                source_file="etl.sql",
                line_start=1,
            ),
        )
    )
    assert "SELEC garbage NOPE (" in out


def test_question_bearing_decision_with_one_option_renders_only_that_option():
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
                options=(
                    Option(
                        label="merge",
                        kind="merge",
                        effect="updates matching rows and inserts the rest",
                    ),
                ),
            ),
        )
    )
    assert "Chose: merge" in out
    assert "    - merge (chosen) — updates matching rows and inserts the rest" in out
    # One option offered, one option rendered -- nothing invented beside it.
    assert len([line for line in out.splitlines() if line.startswith("    - ")]) == 1


def test_every_shipped_option_reaches_the_report_in_both_registers():
    """The guard that keeps the plain register load-bearing. The report is
    the only thing in production that reads `Option.plain`, so if a future
    option ships without a plain wording -- or the renderer stops printing
    one -- this is what fails, rather than the field quietly becoming
    decorative the way `Option.effect` did when it was produced at five
    sites and read by none.

    `tests/unit/passes/test_plain_register.py` already asserts the options
    carry the wording. What is asserted here is the half nothing else
    covers: that it lands on the page, in its own nested list item beneath
    the dbt-register line it restates, and beside that line rather than in
    place of it.
    """
    options = (
        append_option(),
        merge_option(),
        merge_option(("id",)),
        inline_option(),
        var_option("cutoff"),
    )
    out = _report(
        decisions=(
            Decision(
                key="k3",
                tier=2,
                action="chose incremental strategy",
                reason="Tier 2 owns it",
                source_file="etl.sql",
                line_start=9,
                line_end=9,
                question="How should rows arriving again be handled?",
                plain_question="What should happen to a row it has seen before?",
                chosen=options[0].label,
                options=options,
            ),
        )
    )
    assert "  - In plain words: What should happen to a row it has seen before?" in out
    for option in options:
        assert option.plain, f"{option.label} ships with no plain wording"
        assert option.plain != option.effect, f"{option.label} repeats its effect verbatim"
        assert f"    - {option.label}" in out, f"{option.label} is not rendered"
        assert option.effect in out, f"{option.label}'s effect is not rendered"
        assert f"      - In plain words: {option.plain}" in out, (
            f"{option.label}'s plain wording is not rendered under it"
        )


def test_each_register_is_its_own_list_item_not_a_continuation_line():
    """Markdown folds consecutive lines of a list item into one paragraph, so
    an unbulleted "In plain words: ..." under the question would render as
    "... Chose: append every row In plain words: ..." -- one run-on
    sentence. Every restatement has to be a list item of its own.
    """
    out = _report(
        decisions=(
            Decision(
                key="k4",
                tier=2,
                action="chose incremental strategy",
                reason="Tier 2 owns it",
                source_file="etl.sql",
                line_start=9,
                line_end=9,
                question="Append or merge?",
                plain_question="Add every row again, or update the ones already there?",
                chosen="append every row",
                options=(append_option(),),
            ),
        )
    )
    plain = [line for line in out.splitlines() if "In plain words:" in line]
    assert len(plain) == 2, plain
    assert all(line.lstrip().startswith("- ") for line in plain), plain
