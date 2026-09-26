"""What `dbtw convert` records, and what it does with it on the next run.

The tool was stateless, so its tenth conversion of a project explained the
same words in the same place as its first. These tests are about the only
state it keeps -- which dbt words it has already explained, per project -- and
about the two things that state must never do: appear without being asked for,
and vanish without being mentioned.
"""

from __future__ import annotations

import json
from pathlib import Path

from dbtw.cli.main import main
from dbtw.core.progress import FULL_SHOWINGS

ROOT = Path(__file__).parents[2] / "fixtures"
PROJECT = ROOT / "projects" / "jaffle_shop"


def _run(out: Path, state: Path, *extra: str) -> int:
    import os

    os.environ["XDG_STATE_HOME"] = str(state)
    try:
        return main(
            [
                "convert",
                str(ROOT / "sql" / "qualified_etl.sql"),
                "--project",
                str(PROJECT),
                "--out",
                str(out),
                *extra,
            ]
        )
    finally:
        os.environ.pop("XDG_STATE_HOME", None)


def _glossary(out: Path) -> str:
    report = (out / "CONVERSION_REPORT.md").read_text(encoding="utf-8")
    return report.split("## Words this report uses")[1].split("\n## ")[0]


def test_the_first_conversion_explains_and_records(tmp_path: Path) -> None:
    state = tmp_path / "state"

    assert _run(tmp_path / "one", state) == 0

    assert "- **ref()**" in _glossary(tmp_path / "one")
    record = json.loads((state / "dbtw" / "learned.json").read_text(encoding="utf-8"))
    (entry,) = record["projects"].values()
    assert entry["conversions"] == 1
    assert entry["shown"]["ref()"] == 1


def test_the_fourth_conversion_stops_explaining_what_the_first_three_did(tmp_path: Path) -> None:
    """The whole point. Three full explanations, then the word is named and
    its meaning moves below the material that is new.
    """
    state = tmp_path / "state"
    for run in range(FULL_SHOWINGS):
        assert _run(tmp_path / f"run{run}", state) == 0

    assert _run(tmp_path / "after", state) == 0

    assert "- **ref()**" not in _glossary(tmp_path / "after")
    assert "ref()" in _glossary(tmp_path / "after"), "named, so the reader sees it was skipped"
    report = (tmp_path / "after" / "CONVERSION_REPORT.md").read_text(encoding="utf-8")
    assert "- **ref()**" in report.split("## Words you have met before")[1]


def test_no_remember_explains_everything_and_writes_nothing(tmp_path: Path) -> None:
    """The opt-out has to be a real one: a reader who asks the tool to keep no
    record of them must end the run with no file, not with a file it chose not
    to read.
    """
    state = tmp_path / "state"
    for run in range(FULL_SHOWINGS + 1):
        assert _run(tmp_path / f"run{run}", state, "--no-remember") == 0

    assert "- **ref()**" in _glossary(tmp_path / f"run{FULL_SHOWINGS}")
    assert not (state / "dbtw" / "learned.json").exists()


def test_a_damaged_record_is_reported_and_never_written_over(tmp_path: Path) -> None:
    """A conversion is still correct and complete when the record is broken,
    so the run succeeds -- but the reader is told, because a tenth report that
    reads like a first would otherwise look like the tool not working.
    """
    state = tmp_path / "state"
    store = state / "dbtw" / "learned.json"
    store.parent.mkdir(parents=True)
    store.write_text("{ this is not json", encoding="utf-8")

    assert _run(tmp_path / "one", state) == 0

    assert store.read_text(encoding="utf-8") == "{ this is not json", "left exactly as found"
    report = (tmp_path / "one" / "CONVERSION_REPORT.md").read_text(encoding="utf-8")
    assert "could not be read" in report
    assert "- **ref()**" in _glossary(tmp_path / "one")


def test_two_projects_keep_separate_curricula(tmp_path: Path) -> None:
    """Her team's project and her own are two curricula; one shared count
    would retire a word in a project she has never converted into.
    """
    state = tmp_path / "state"
    for run in range(FULL_SHOWINGS):
        assert _run(tmp_path / f"run{run}", state) == 0

    other = tmp_path / "other_project"
    other.mkdir()
    (other / "dbt_project.yml").write_text(
        (PROJECT / "dbt_project.yml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    import os

    os.environ["XDG_STATE_HOME"] = str(state)
    try:
        assert (
            main(
                [
                    "convert",
                    str(ROOT / "sql" / "qualified_etl.sql"),
                    "--project",
                    str(other),
                    "--out",
                    str(tmp_path / "elsewhere"),
                ]
            )
            == 0
        )
    finally:
        os.environ.pop("XDG_STATE_HOME", None)

    assert "- **ref()**" in _glossary(tmp_path / "elsewhere"), "a new project explains everything"

    # And the first project still knows what it knew. One file holds every
    # project, so a save that replaced it rather than merging into it would
    # pass the assertion above -- a fresh project explains everything either
    # way -- while silently resetting a curriculum three conversions deep.
    assert _run(tmp_path / "back", state) == 0
    assert "- **ref()**" not in _glossary(tmp_path / "back")
