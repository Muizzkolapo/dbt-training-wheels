from pathlib import Path

from dbtw.core.assemble import AssembledModel, ProjectChange, SourceEntry, Variable
from dbtw.core.context import read_project
from dbtw.core.emit.report import render_report
from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import Decision, ModelDescription, SchemaTest
from dbtw.core.teach import GLOSSARY, terms_in

GLOSSARY_HEADING = "## Words this report uses"

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
        GLOSSARY_HEADING,
        "## Your project's conventions",
        "## Models",
        "## One model, as dbt reads it",
        "## Sources",
        "## Decisions",
        "## Still pending",
        "## Not done yet",
        "## Then, in your project",
    ]


def test_summary_counts_and_dialect():
    out = _report()
    assert "jaffle_shop" in out
    assert "tsql" in out


def test_summary_shows_zero_tests_when_none_were_recorded():
    """Derived, always rendered -- 0 as 0, never omitted just because the
    conversion asked for none (spec §11.4(c))."""
    assert "- **Tests**: 0" in _report()


def test_summary_shows_the_recorded_tests_count():
    out = _report(tests=(SchemaTest("stg_orders", "order_id"),))
    assert "- **Tests**: 1" in out


def test_summary_says_how_many_models_the_reader_described():
    """Derived and always rendered, the way the tests count is.

    The report is what a reviewer reads before approving the conversion, and
    a description is the one thing in it no engine wrote. A report that
    listed the files but never said how many models carry one would be the
    only place a reviewer could not tell a described conversion from an
    undescribed one without opening every .yml.
    """
    assert "- **Described models**: 0 of 1" in _report()

    out = _report(descriptions=(ModelDescription("stg_orders", "One row per order."),))

    assert "- **Described models**: 1 of 1" in out


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
    # `INTEGER`, not the `INT` the reader typed: the excerpt is the statement
    # as sqlglot reads it, which is how this report renders every model body
    # too. The alternative -- showing the original text with comments cut out
    # by hand -- is a comment-and-quote scanner, and the one this replaced hid
    # real SQL on a backslash-escaped quote, a multi-line string literal, a
    # dollar-quoted body and a bracketed identifier. See core/excerpt.py.
    assert "DECLARE @d INTEGER = 1" in out


def test_empty_pending_says_so():
    assert "Nothing — every statement was handled." in _report()


def test_not_done_yet_states_references_are_rewritten_and_names_deferred_work():
    out = _report(models=(_incremental_model(),))
    assert "Table references are not yet rewritten as ref() or source() calls." not in out
    # No variable in this change, so the sentence names no var() call.
    assert "This tool rewrote 2 table references as ref() or source() calls." in out
    assert "var()" not in out.split("## Not done yet")[1]
    assert "incremental model" in out


def test_no_sources_says_none_to_declare():
    assert "None to declare." in _report(sources=())


# --- the glossary: the dbt words this report uses, defined once, in it


def _split_glossary(out: str) -> tuple[str, list[tuple[str, str]]]:
    """The report with its glossary section cut out, and the section's
    entries as (name, definition) pairs.

    Cut out rather than searched in place: the section is the one part of the
    report that must be compared against the rest, and a term is trivially
    "in the report" once its own definition has been printed there.
    """
    lines = out.splitlines()
    start = lines.index(GLOSSARY_HEADING)
    # Defaulting to the end of the report rather than raising: where the
    # section sits is what `test_the_glossary_sits_ahead_of_...` is for, and a
    # helper that blew up when it moved would make that one failure look like
    # four unrelated errors.
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    rest = "\n".join(lines[:start] + lines[end:])
    defined: list[tuple[str, str]] = []
    for line in lines[start:end]:
        if not line.startswith("- **"):
            continue
        name, _, plain = line.removeprefix("- **").partition("** — ")
        defined.append((name, plain))
    return rest, defined


def test_the_report_defines_every_dbt_word_it_uses_and_no_others():
    """The whole point of `terms_in` over one rendered block. Four of five
    personas never reached the round-1 glossary that dumped every definition,
    so a section listing words this report does not use is not a harmless
    extra -- it is what made the section unread.

    The definitions are compared in full, not by name: a report that reworded
    one would be a second explanation of a word the engine has already
    defined, which is what this module exists to prevent.
    """
    rest, defined = _split_glossary(_report())
    used = terms_in(rest)
    assert used, "this report uses no dbt vocabulary at all; it proves nothing here"
    assert defined == [(term.name, term.plain) for term in used]


def test_the_report_defines_materialized_in_both_the_forms_it_prints_it_in():
    """4/5 personas then 3/3 could not read this word, and every report puts
    it in front of them twice over: the models table heads a column
    `Materialization`, and the conventions section keys detections on
    `layer.*.materialization`. Those are one word in two forms, which is why
    the glossary carries the second as a spelling of the first rather than as
    a second entry free to drift from it.
    """
    out = _report()
    rest, _ = _split_glossary(out)
    (term,) = [t for t in GLOSSARY if t.name == "materialized"]
    # Not vacuous, and not resting on either form alone: both are really in
    # the report, outside the section that would otherwise supply the word.
    assert "| Model | Layer | Materialization | Depends on |" in rest
    assert "layer.staging.materialization" in rest
    assert f"- **{term.name}** — {term.plain}" in out


def test_the_report_names_the_commands_and_so_defines_them():
    """This reverses a decision, on evidence, and the reasoning is worth
    keeping because the old reasoning was not wrong when it was written.

    It said: the four dbt commands are recommended by the screens, not by the
    report, and defining them here would be the round-1 dump again, four
    definitions deep. That was a fair trade while every report defined every
    word it used on every run -- four extra definitions in every report
    forever really is a dump.

    What changed is that a word is now explained three times and then folded
    (see `core.progress`), so naming the commands costs four definitions in a
    reader's first three conversions and a named list after that. And what
    the old trade cost was measured: over ten real conversions a reader of
    the CLI report met 8 of the glossary's 14 words and was never told how to
    run anything. Two of the four questions the persona's comprehension check
    asks -- when does this run again, and what would `dbt build` do to it --
    were unanswerable from the report, while the walk's last screen had named
    and defined all four commands since it was built.

    The old trade does come back for a reader who passes `--no-remember`:
    nothing is folded for them, so these four are defined in full in every
    report they ever generate. That is the flag doing exactly what it says --
    "explain every dbt word in full, and record nothing" -- and it was already
    true of the other ten words before this change. It is worth saying out
    loud rather than leaving implicit, because it is precisely the dump the
    reasoning above traded away, and the trade only holds while the fold is on.
    """
    out = _report()
    (term,) = [t for t in GLOSSARY if t.name == "dbt build"]
    rest, _ = _split_glossary(out)
    assert "dbt build" in rest, "named outside the glossary is what makes it defined inside it"
    assert f"- **{term.name}** — {term.plain}" in out


def test_the_glossary_sits_ahead_of_the_first_section_that_uses_one_of_the_words():
    """A reader who has to go looking for a definition has been measured not
    to look. The conventions section immediately below already says
    "materialization" and "staging"."""
    out = _report()
    lines = out.splitlines()
    heading_at = lines.index(GLOSSARY_HEADING)
    conventions_at = lines.index("## Your project's conventions")
    assert heading_at < conventions_at
    # Not vacuous: the section it sits ahead of is one that needs it.
    conventions = "\n".join(lines[conventions_at:])
    assert {t.name for t in terms_in(conventions)} >= {"materialized", "staging"}


def _incremental_model() -> AssembledModel:
    return AssembledModel(
        name="fct_events",
        path="models/marts/fct_events.sql",
        # Two references that assemble resolved: one to a model, one to a source.
        body=(
            "SELECT id FROM {{ ref('stg_orders') }} JOIN {{ source('raw', 'events') }} USING (id)"
        ),
        materialization="incremental",
        grants=(),
        layer="marts",
        depends_on=(),
        leading_comments=(),
        source_indices=(1,),
        incremental_strategy="append",
    )


def _unresolved(model: str, table: str) -> Decision:
    """The Decision the assembler records for a table reference it left as written."""
    return Decision(
        key=f"assemble.rewrite_unresolved.{model}.{table}",
        tier=2,
        action=f"{table} in {model} left as written: could not resolve it to ref() or source()",
        reason="no model or source matches this name",
        source_file="",
        line_start=0,
        line_end=0,
    )


def _not_done_yet(**kw) -> str:
    """The "Not done yet" section of the report for this change, and nothing else."""
    out = _report(**kw)
    return out.split("## Not done yet\n")[1].split("\n## ")[0]


# A conversion that built an incremental model, declared a test that an answer
# asked for, rewrote two table references, left one as written and kept a
# script variable.
_EVERYTHING = dict(
    models=(_change().models[0], _incremental_model()),
    tests=(SchemaTest("fct_events", "id"),),
    decisions=(*_change().decisions, _unresolved("stg_orders", "legacy.orders")),
    variables=(Variable("cutoff", "'2024-01-01'", "etl.sql", 2),),
)


def _every_form() -> tuple[str, ...]:
    """The section for each case of the references: none at all, no models,
    every one unresolved, every one rewritten, and some of each."""
    return (
        _not_done_yet(),
        _not_done_yet(models=()),
        _not_done_yet(decisions=(_unresolved("stg_orders", "legacy.orders"),)),
        _not_done_yet(models=(_incremental_model(),)),
        _not_done_yet(**_EVERYTHING),
    )


def test_not_done_yet_names_the_incremental_models_this_run_built():
    section = _not_done_yet(**_EVERYTHING)

    assert "fct_events" in section
    assert "built 1 incremental model" in section
    assert "leaves these items for later: incremental" not in section


def test_not_done_yet_says_no_incremental_model_was_built_when_none_was():
    section = _not_done_yet()

    assert "built no incremental models" in section
    assert "fct_events" not in section


def test_not_done_yet_counts_the_tests_an_answer_asked_for():
    """The summary says "Tests: 1". The closing section must not say the
    opposite a page later."""
    section = _not_done_yet(**_EVERYTHING)

    assert "did not create tests" not in section
    assert "only the tests that your answers asked for: 1" in section
    assert "did not create documentation blocks or exposures" in section


def test_not_done_yet_says_no_tests_were_created_when_none_were():
    section = _not_done_yet()

    assert "did not create tests, documentation blocks or exposures" in section
    assert "answers asked for" not in section


def test_not_done_yet_counts_both_the_rewritten_and_the_unresolved_references():
    section = _not_done_yet(**_EVERYTHING)

    assert "This tool rewrote 2 table references as ref() or source() calls." in section
    assert "left 1 table reference as written" in section


def test_not_done_yet_claims_every_reference_was_rewritten_only_when_it_was():
    section = _not_done_yet(models=(_incremental_model(),))

    assert "This tool rewrote 2 table references as ref() or source() calls." in section
    assert "as written" not in section


def test_not_done_yet_claims_no_rewrite_when_the_models_reference_no_table():
    """`SELECT 1 AS a` reads no table, so nothing became ref() or source()."""
    section = _not_done_yet()

    assert "rewrote" not in section
    assert "This conversion found no table reference to rewrite" in section


def test_not_done_yet_claims_no_rewrite_when_there_are_no_models():
    section = _not_done_yet(models=())

    assert "rewrote" not in section
    assert "This conversion found no table reference to rewrite" in section


def test_not_done_yet_claims_no_rewrite_when_every_reference_was_unresolved():
    section = _not_done_yet(decisions=(_unresolved("stg_orders", "legacy.orders"),))

    assert "rewrote" not in section
    assert "left 1 table reference as written" in section
    assert "found no table reference" not in section


def test_not_done_yet_names_var_calls_only_when_the_run_kept_a_variable():
    assert "var()" in _not_done_yet(**_EVERYTHING)
    assert "var()" not in _not_done_yet()


def test_not_done_yet_keeps_every_caveat_that_is_true_of_every_run():
    for section in _every_form():
        assert "This tool did no validation." in section
        assert "did not start dbt, compiled nothing" in section
        assert "ran nothing against a warehouse" in section
        assert "temporary tables" in section
        assert "documentation blocks or exposures" in section
        assert "review the model bodies and the decisions above" in section


def test_the_closing_section_alone_guarantees_the_glossary_is_never_empty():
    """Why `_render_glossary` has no empty case to handle: "Not done yet" is
    rendered by every report, whatever the conversion did, and names four of
    these words by itself in each of its forms. If it is ever reworded past
    them, this fails here rather than leaving a heading with nothing under it
    in every report."""
    for section in _every_form():
        assert {t.name for t in terms_in(section)} >= {
            "warehouse",
            "ref()",
            "source()",
            "incremental",
        }
