"""The report says how to run what it just wrote.

Found by scoring the persona's comprehension check against ten real
conversions. The check asks four questions of a model file -- what does this
build, what does it read, when does it run again, and what would `dbt build`
do to it -- and after ten conversions a reader of the CLI report could answer
two. The other two are about running dbt, and no report ever named a dbt
command: the reader met 8 of the glossary's 14 words, and the six they never
met included all four commands.

The web's last screen had named and defined them since the walk was built.
One tool, two surfaces, and only one of them ever said how to run anything.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.emit.test_report import _change

from dbtw.core.context import read_project
from dbtw.core.emit.report import NEXT_HEADING, render_report
from dbtw.core.progress import FULL_SHOWINGS, Progress
from dbtw.core.teach import COMMANDS, GLOSSARY

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _report(**kw: object) -> str:
    return render_report(_change(**kw), read_project(FIXTURES / "jaffle_shop"))


def test_every_command_is_named() -> None:
    report = _report()

    for command in COMMANDS:
        assert command in report.split(NEXT_HEADING)[1], f"{command} is not named"


def test_the_commands_are_the_four_and_in_the_order_a_first_run_goes() -> None:
    """Five of five personas asked for the set related to one another rather
    than introduced one at a time. The order is the relation: see what would
    be sent, send it, check it, and the one that does both.

    Written out rather than read back from `COMMANDS`. Comparing the constant
    against itself sorted by where it appears is a tautology that holds for
    any order and any subset -- both were proven to survive by mutation, which
    is how this test came to be written this way.
    """
    assert COMMANDS == ("dbt compile", "dbt run", "dbt test", "dbt build")

    section = _report().split(NEXT_HEADING)[1]
    assert list(COMMANDS) == sorted(COMMANDS, key=section.index)


def test_naming_them_is_what_makes_the_glossary_define_them() -> None:
    """The glossary is read off the report, so a command the report never
    said was a word it never defined -- which is why ten conversions left a
    reader at 8 of 14 with no way to reach the rest.
    """
    report = _report()

    for command in COMMANDS:
        term = next(t for t in GLOSSARY if t.name == command)
        assert f"- **{term.name}** — {term.plain}" in report


def test_the_report_says_that_nothing_schedules_them() -> None:
    """One of the four things the persona believes at the start that is wrong
    is that dbt is a scheduler. Nothing in the conversion contradicted it.
    """
    section = _report().split(NEXT_HEADING)[1]

    assert "schedule" in section.casefold()


def test_the_section_does_not_repeat_the_definitions() -> None:
    """The glossary defines each word once, wherever it is rendered. A second
    copy here is a second thing to keep in step with the first.
    """
    section = _report().split(NEXT_HEADING)[1]

    for term in (t for t in GLOSSARY if t.name in COMMANDS):
        assert term.plain not in section


def test_a_reader_who_has_met_the_commands_is_still_told_them() -> None:
    """The fold retires a *definition*, never the step. A tenth conversion
    still has to say what to run -- it is the one thing the report is for
    once the files are written.
    """
    met = Progress(conversions=9, shown={term.name: FULL_SHOWINGS for term in GLOSSARY})

    report = render_report(_change(), read_project(FIXTURES / "jaffle_shop"), progress=met)

    for command in COMMANDS:
        assert command in report.split(NEXT_HEADING)[1]
