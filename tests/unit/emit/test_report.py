from pathlib import Path

from dbtw.core.assemble import AssembledModel, ProjectChange, SourceEntry
from dbtw.core.context import read_project
from dbtw.core.emit.report import _NOT_DONE_YET, render_report
from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import Decision, SchemaTest
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
        "## Sources",
        "## Decisions",
        "## Still pending",
        "## Not done yet",
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


def test_not_done_yet_states_references_are_rewritten_and_names_deferred_work():
    out = _report()
    assert "Table references are not yet rewritten as ref() or source() calls." not in out
    assert "Table references and script variables have been rewritten as ref(), source()," in out
    assert "Incremental models" in out


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


def test_the_report_does_not_define_a_command_it_never_mentions():
    """The four dbt commands are recommended by the screens, not by the
    report -- over a real conversion the report names none of them. Defining
    them here would be the round-1 dump again, four definitions deep."""
    out = _report()
    (term,) = [t for t in GLOSSARY if t.name == "dbt build"]
    rest, _ = _split_glossary(out)
    assert "dbt build" not in rest
    assert term.plain not in out
    assert "- **dbt build**" not in out


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


def test_the_closing_section_alone_guarantees_the_glossary_is_never_empty():
    """Why `_render_glossary` has no empty case to handle: `_NOT_DONE_YET` is
    rendered by every report, whatever the conversion did, and names four of
    these words by itself. If it is ever reworded past them, this fails here
    rather than leaving a heading with nothing under it in every report."""
    assert {t.name for t in terms_in(_NOT_DONE_YET)} >= {
        "warehouse",
        "ref()",
        "source()",
        "incremental",
    }
