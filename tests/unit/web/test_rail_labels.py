"""Two question steps in the rail never share a label.

Each question step is named by its table and a kind word ("revenue_daily
append", "revenue_daily layer"). Two questions of one kind about one table
would still share that label, and a reader could not tell which step is
which. Those are numbered, in rail order; every other step keeps its label.
"""

from __future__ import annotations

from dbtw.web.app import Screen, _number_repeats


def _q(url: str, table: str, kind: str) -> Screen:
    return Screen(url=url, label=table, engine=True, state="ask", kind=kind)


def test_questions_of_one_kind_about_one_table_are_numbered_in_rail_order() -> None:
    rail = _number_repeats(
        (
            _q("/questions/0", "revenue_daily", "append"),
            _q("/questions/1", "revenue_daily", "layer"),
            _q("/questions/2", "revenue_daily", "append"),
        )
    )

    assert [(s.label, s.kind, s.nth) for s in rail] == [
        ("revenue_daily", "append", "1"),
        ("revenue_daily", "layer", ""),
        ("revenue_daily", "append", "2"),
    ]


def test_a_step_whose_label_is_unique_is_not_numbered() -> None:
    rail = _number_repeats(
        (
            _q("/questions/0", "revenue_daily", "append"),
            _q("/questions/1", "revenue_events", "append"),
            Screen(url="/done", label="Done", engine=False),
        )
    )

    assert [s.nth for s in rail] == ["", "", ""]


def test_every_label_in_a_numbered_rail_is_distinct() -> None:
    rail = _number_repeats(
        tuple(_q(f"/questions/{i}", "t", "merge") for i in range(3))
        + (_q("/questions/3", "t", ""), _q("/questions/4", "t", ""))
    )

    shown = [" ".join(p for p in (s.label, s.kind, s.nth) if p) for s in rail]
    assert len(set(shown)) == len(shown), shown


def test_the_walk_numbers_its_own_question_steps() -> None:
    """Through `_screens`, the function every page builds its rail from, so
    the numbering cannot be dropped from the walk while its unit tests stay
    green. No current script asks two questions of one kind about one
    table, so the questions are built here; `_screens` reads only their
    keys, subjects and options.
    """
    from types import SimpleNamespace

    from dbtw.core.passes import Decision, Subject
    from dbtw.web.app import _screens

    def ask(index: int) -> Decision:
        return Decision(
            key=f"tier2.append.t.sql:{index}",
            tier=2,
            action="a",
            reason="r",
            source_file="t.sql",
            line_start=1,
            line_end=1,
            question="q?",
            subject=Subject(table="revenue_daily"),
        )

    view = SimpleNamespace(questions=(ask(0), ask(1)))

    steps = [s for s in _screens(view) if s.url.startswith("/questions/")]  # type: ignore[arg-type]

    assert [(s.label, s.kind, s.nth) for s in steps] == [
        ("revenue_daily", "append", "1"),
        ("revenue_daily", "append", "2"),
    ]
