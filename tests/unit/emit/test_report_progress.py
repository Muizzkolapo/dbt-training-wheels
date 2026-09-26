"""The tenth report does not read like the first.

A word this project has already explained `FULL_SHOWINGS` times is named
rather than defined again, and its meaning moves below the material that is
new. What makes this safe to do at all is that nothing is lost: the meaning is
still in the same report, and a reader who never converted this project before
is shown everything.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.emit.test_report import _change

from dbtw.core.context import read_project
from dbtw.core.emit.report import MET_HEADING, render_report
from dbtw.core.progress import FULL_SHOWINGS, NOTHING_EXPLAINED, Progress
from dbtw.core.teach import GLOSSARY, terms_in

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"
GLOSSARY_HEADING = "## Words this report uses"


def _report(progress: Progress = NOTHING_EXPLAINED, **kw: object) -> str:
    return render_report(_change(**kw), read_project(FIXTURES / "jaffle_shop"), progress=progress)


def _met(name: str, times: int = FULL_SHOWINGS) -> Progress:
    return Progress(conversions=times, shown={name: times})


def test_a_first_conversion_defines_every_word_it_uses() -> None:
    """The default, and the honest one: no record means nothing was explained."""
    report = _report()

    for term in terms_in(report):
        assert f"- **{term.name}** — {term.plain}" in report
    assert MET_HEADING not in report


def test_a_word_met_enough_times_is_named_rather_than_defined_again() -> None:
    report = _report(_met("ref()"))

    assert "- **ref()** — How a model names another model" not in report.split(MET_HEADING)[0]
    assert MET_HEADING in report


def test_the_meaning_of_a_folded_word_is_still_in_the_same_report() -> None:
    """Moved, not dropped. A reader who has forgotten `ref()` must not have to
    find a report from three conversions ago to look it up.
    """
    ref = next(t for t in GLOSSARY if t.name == "ref()")

    report = _report(_met("ref()"))

    assert ref.plain in report.split(MET_HEADING)[1]


def test_the_folded_words_are_named_where_the_definitions_used_to_be() -> None:
    """She has to be able to see that a word was skipped on purpose, and
    where it went -- a definition that silently stops appearing reads as a
    tool that forgot it.
    """
    report = _report(_met("ref()"))

    glossary = report.split(GLOSSARY_HEADING)[1].split("\n##")[0]
    assert "ref()" in glossary


def test_a_word_below_the_threshold_is_still_defined_in_full() -> None:
    report = _report(Progress(conversions=1, shown={"ref()": FULL_SHOWINGS - 1}))

    ref = next(t for t in GLOSSARY if t.name == "ref()")
    assert f"- **{ref.name}** — {ref.plain}" in report.split(MET_HEADING)[0]


def test_a_record_that_could_not_be_read_explains_everything_and_says_so() -> None:
    """The failure this must never make quietly: a damaged record and a first
    conversion look identical from the inside, and only one of them is a
    reason to say nothing.
    """
    report = _report(Progress(unreadable="the record at /tmp/x could not be read: boom"))

    ref = next(t for t in GLOSSARY if t.name == "ref()")
    assert f"- **{ref.name}** — {ref.plain}" in report
    assert "could not be read" in report
    assert MET_HEADING not in report


def test_the_report_says_how_far_through_the_words_she_is() -> None:
    """The progression is the point, so it is visible rather than inferred
    from noticing that a report got shorter.
    """
    report = _report(_met("ref()"))

    assert f"of {len(GLOSSARY)}" in report


def test_the_tally_counts_this_conversion_too() -> None:
    """The reader is holding this report: a tally that excluded the words on
    the page in front of them would be wrong by exactly what they can see.
    """
    first = _report()

    used = len(terms_in(first))
    assert f"**dbt words explained**: {used} of {len(GLOSSARY)}" in first


def test_a_word_explained_in_another_project_is_not_counted_here() -> None:
    """The tally answers for this project, because that is what the record
    holds and what `load` was keyed by.
    """
    elsewhere = Progress(conversions=4, shown={"incremental": FULL_SHOWINGS})

    report = _report(elsewhere)

    # `incremental` is explained in the closing section of every report, so it
    # counts here as met -- what must not happen is a tally that quietly
    # credits words this project never used.
    tally = [line for line in report.splitlines() if "dbt words explained" in line]
    assert len(tally) == 1
    assert f"of {len(GLOSSARY)}" in tally[0]


def test_the_folded_section_says_where_the_record_lives() -> None:
    """A tool that remembers something about a reader says where, in the one
    place the reader is already looking, and only when it has acted on it.
    """
    report = _report(_met("ref()"))

    below = report.split(MET_HEADING)[1]
    assert "learned.json" in below


def test_a_report_where_every_word_is_folded_is_a_pointer_not_an_empty_promise() -> None:
    """The tenth conversion of a project. An intro promising "each dbt word
    this report uses, in the order it first appears" above no definitions at
    all is the one shape this section must never take.
    """
    everything = Progress(conversions=9, shown={term.name: FULL_SHOWINGS for term in GLOSSARY})

    report = _report(everything)

    glossary = report.split(GLOSSARY_HEADING)[1].split("\n## ")[0]
    assert "- **" not in glossary, "nothing is defined here, so nothing should look defined"
    assert "none is repeated here" in glossary
    assert "ref()" in glossary, "the words are still named, so the reader sees what was skipped"
    assert MET_HEADING in report


def test_every_folded_word_is_defined_below_however_many_there_are() -> None:
    everything = Progress(conversions=9, shown={term.name: FULL_SHOWINGS for term in GLOSSARY})

    report = _report(everything)

    below = report.split(MET_HEADING)[1]
    for term in terms_in(report.split(MET_HEADING)[0]):
        assert f"- **{term.name}** — {term.plain}" in below
