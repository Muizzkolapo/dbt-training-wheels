"""The report says nothing that another part of the same report contradicts.

Testers found four such places on main:

1. "The 'Still pending' section above lists each statement..." next to
   "Nothing — every statement was handled."
2. "This tool still leaves these items for later: temporary tables, and
   UPDATE..." in every report, also when the input had neither.
3. "in the order of a first run", then `dbt build` listed after `dbt run`
   and `dbt test`, although `dbt build` does the work of both.
4. "dbt words explained: 12 of 14" with no explanation, also under
   --no-remember, where this tool records nothing.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.emit.test_report import _change

from dbtw.core.context import read_project
from dbtw.core.emit.report import NEXT_HEADING, render_report
from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.progress import Progress

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _report(**kw: object) -> str:
    named = {"remember": kw.pop("remember")} if "remember" in kw else {}
    return render_report(_change(**kw), read_project(FIXTURES / "jaffle_shop"), Progress(), **named)  # type: ignore[arg-type]


def _section(report: str, heading: str) -> str:
    return report.split(heading)[1].split("\n## ")[0]


def _update_pending() -> tuple[tuple[int, ClassifiedStatement], ...]:
    raw = RawStatement(
        source_file="etl.sql", index=4, text="UPDATE t SET a = 1", line_start=9, line_end=9
    )
    return ((4, ClassifiedStatement(raw=raw, kind="update", reason="UPDATE mutates rows")),)


def test_with_nothing_pending_no_section_claims_to_list_a_pending_statement() -> None:
    report = _report()

    assert "Nothing — every statement was handled." in _section(report, "## Still pending")
    assert "lists each statement" not in report
    assert "Still pending" not in _section(report, "## Not done yet")


def test_with_a_pending_statement_the_closing_section_points_to_the_list() -> None:
    report = _report(pending=_update_pending())

    assert "Still pending" in _section(report, "## Not done yet")


def test_with_no_temporary_table_or_update_the_report_does_not_name_them() -> None:
    section = _section(_report(), "## Not done yet")

    assert "temporary tables" not in section
    assert "UPDATE" not in section


def test_the_four_commands_are_two_ways_and_build_is_not_a_fourth_step() -> None:
    section = _section(_report(), NEXT_HEADING)

    assert "in the order of a first" not in section
    assert "4. `dbt build`" not in section, "build is an alternative, not a step after test"
    assert "1. `dbt compile`" in section
    assert "2. `dbt run`" in section
    assert "3. `dbt test`" in section
    (build_line,) = [line for line in section.splitlines() if "`dbt build`" in line]
    assert "`dbt run`" in build_line and "`dbt test`" in build_line, build_line


def test_the_word_count_says_what_it_counts() -> None:
    (tally,) = [line for line in _report().splitlines() if "dbt words explained" in line]

    assert "earlier" in tally, tally


def test_with_no_record_the_report_gives_no_word_count() -> None:
    assert "dbt words explained" not in _report(remember=False)
