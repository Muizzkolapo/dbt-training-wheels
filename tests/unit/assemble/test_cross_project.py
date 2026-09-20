"""Reading a table from another team's model instead of declaring it here.

The design's step 5: "if another team has modelled a table, point at their
model instead of re-declaring the raw table as your source. You inherit their
tests and their lineage; you also inherit their breakages."

Everything here turns on that being a *proposal*. Two projects can call two
different tables `customers`, and nothing in a name match can tell them
apart -- so a match is a question, the default is the answer that depends on
nobody, and the evidence is on the Decision so the reader decides with it.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.assemble.helpers import convert

from dbtw.core.context import read_project
from dbtw.core.passes import answer_for

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"

# `core_platform` builds a model called dim_customers. This reads a table of
# that name, and another of a name nobody else builds.
SQL = (
    "INSERT INTO revenue_events SELECT c.name, o.total "
    "FROM analytics.dim_customers AS c JOIN analytics.orders AS o ON o.cid = c.customer_id;\n"
)


def _other():  # type: ignore[no-untyped-def]
    return (read_project(FIXTURES / "core_platform"),)


def _question(change):  # type: ignore[no-untyped-def]
    return next(d for d in change.decisions if d.key.startswith("assemble.cross_ref."))


def test_a_table_another_project_models_is_asked_about_and_not_acted_on() -> None:
    change = convert(SQL, elsewhere=_other())

    question = _question(change)

    assert question.key == "assemble.cross_ref.dim_customers"
    assert [option.kind for option in question.options] == ["source", "cross_ref"]
    assert question.chosen == "declare dim_customers as my source"
    # Unanswered, nothing has changed: the table is still this project's source.
    assert ("analytics", "dim_customers") in {(s.source_name, s.table) for s in change.sources}
    assert "source('analytics', 'dim_customers')" in change.models[0].body


def test_the_question_carries_the_evidence_it_was_raised_on() -> None:
    """Which project, which model, and where that model's file is. A reader
    deciding whether two tables are the same table needs the other one named,
    not a claim that a match exists.
    """
    change = convert(SQL, elsewhere=_other())

    reason = _question(change).reason

    assert "core_platform" in reason
    assert "dim_customers" in reason
    assert "models/marts/dim_customers.sql" in reason


def test_answering_reads_it_from_their_project_and_stops_declaring_it_here() -> None:
    """Both halves. A body rewritten to their model while sources.yml still
    declared the raw table would tell a reader, in a file they commit, that
    this conversion depends on something it does not read.
    """
    pristine = convert(SQL, elsewhere=_other())
    question = _question(pristine)

    change = convert(
        SQL, elsewhere=_other(), answers={question.key: answer_for(question, "cross_ref")}
    )

    assert "ref('core_platform', 'dim_customers')" in change.models[0].body
    declared = {(s.source_name, s.table) for s in change.sources}
    assert ("analytics", "dim_customers") not in declared
    # And the table nobody else builds is untouched by any of it.
    assert ("analytics", "orders") in declared
    assert "source('analytics', 'orders')" in change.models[0].body


def test_a_table_no_other_project_builds_is_not_asked_about() -> None:
    change = convert(SQL, elsewhere=_other())

    asked = [d.key for d in change.decisions if d.key.startswith("assemble.cross_ref.")]

    assert asked == ["assemble.cross_ref.dim_customers"]


def test_a_reader_who_named_no_other_project_is_asked_nothing() -> None:
    """The ordinary case, and the design's own empty state: every table your
    SQL reads lives in a project you own.
    """
    change = convert(SQL)

    assert not [d for d in change.decisions if d.key.startswith("assemble.cross_ref.")]
    assert ("analytics", "dim_customers") in {(s.source_name, s.table) for s in change.sources}
