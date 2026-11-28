"""A second write into the same out_dir.

The case that found this: a reader wrote the conversion, changed the answer
that placed a model -- `revenue_daily` from models/marts/ to
models/intermediate/ -- and wrote again. The out_dir then held the model
twice, once in each directory, which is two models with one name and a
project dbt refuses to parse. Delivery copies the out_dir into the reader's
project, so the stale copy went with it.

What a second write owes the first: every file the first produced that this
one does not is removed, and said so. What it owes the reader: a file this
tool did not write is never touched, and a file it did write that the reader
has since edited is refused rather than deleted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit.assemble.helpers import context_for, convert

from dbtw.core.emit import (
    REPORT_NAME,
    WRITE_RECORD_NAME,
    StaleOutputError,
    emit,
    written_by_last_emit,
)
from dbtw.core.passes import answer_for

THREE = "three_layers"

# The first model reads a raw table; the second reads the first and nothing
# reads it, so the engine proposes marts for it and asks.
CHAIN = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders;\n"
    "INSERT INTO revenue_daily SELECT order_id, amount FROM revenue_events;\n"
)
DESCRIBED = {"revenue_daily": "One row per day of revenue."}

MART = "models/marts/revenue_daily.sql"
MART_YML = "models/marts/revenue_daily.yml"
INTERMEDIATE = "models/intermediate/revenue_daily.sql"
INTERMEDIATE_YML = "models/intermediate/revenue_daily.yml"


def _in_marts():  # type: ignore[no-untyped-def]
    return convert(CHAIN, project=THREE, descriptions=DESCRIBED)


def _in_intermediate():  # type: ignore[no-untyped-def]
    pristine = _in_marts()
    question = next(d for d in pristine.decisions if d.key.startswith("assemble.layer."))
    return convert(
        CHAIN,
        project=THREE,
        descriptions=DESCRIBED,
        answers={question.key: answer_for(question, "intermediate")},
    )


def _files(out: Path) -> set[str]:
    return {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}


def test_a_second_write_removes_the_model_the_first_wrote_in_another_layer(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    ctx = context_for(THREE)
    emit(_in_marts(), ctx, out)
    assert (out / MART).is_file() and (out / MART_YML).is_file()

    result = emit(_in_intermediate(), ctx, out)

    assert not (out / MART).exists(), "the model is still in the layer it left"
    assert not (out / MART_YML).exists(), "its .yml is still in the layer it left"
    assert (out / INTERMEDIATE).is_file()
    assert (out / INTERMEDIATE_YML).is_file()
    assert sorted(p.relative_to(out).as_posix() for p in result.removed) == [MART, MART_YML]
    # One model of each name in the whole tree: the thing dbt checks.
    names = [p.stem for p in out.rglob("*.sql")]
    assert sorted(names) == ["revenue_daily", "revenue_events"]


def test_the_removal_is_said_in_the_report_and_in_emits_own_decisions(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = context_for(THREE)
    emit(_in_marts(), ctx, out)

    result = emit(_in_intermediate(), ctx, out)

    removed = [d for d in result.decisions if d.key.startswith("emit.removed.")]
    assert sorted(d.key for d in removed) == [f"emit.removed.{MART}", f"emit.removed.{MART_YML}"]
    report = (out / REPORT_NAME).read_text(encoding="utf-8")
    for decision in removed:
        assert decision.action in report


def test_a_file_this_tool_never_wrote_survives_a_second_write(tmp_path: Path) -> None:
    """The out_dir is the reader's directory too. A note they keep there, or
    a model of their own in the very directory a stale model is removed from,
    was never written by this tool and is never removed by it.
    """
    out = tmp_path / "out"
    ctx = context_for(THREE)
    emit(_in_marts(), ctx, out)
    notes = out / "notes.txt"
    notes.write_text("mine\n", encoding="utf-8")
    beside = out / "models" / "marts" / "my_own_model.sql"
    beside.write_text("select 2\n", encoding="utf-8")

    result = emit(_in_intermediate(), ctx, out)

    assert notes.read_text(encoding="utf-8") == "mine\n"
    assert beside.read_text(encoding="utf-8") == "select 2\n"
    assert notes not in result.removed and beside not in result.removed


def test_a_stale_file_the_reader_edited_is_refused_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    """Edited since it was written, it is no longer the file this tool wrote.
    Deleting it would lose the reader's work; keeping it in silence would
    leave the duplicate model this whole change exists to remove. So the
    write refuses, names the file, and writes nothing.
    """
    out = tmp_path / "out"
    ctx = context_for(THREE)
    emit(_in_marts(), ctx, out)
    edited = out / MART
    edited.write_text(edited.read_text(encoding="utf-8") + "-- my change\n", encoding="utf-8")

    with pytest.raises(StaleOutputError, match=MART):
        emit(_in_intermediate(), ctx, out)

    assert edited.read_text(encoding="utf-8").endswith("-- my change\n")
    assert not (out / INTERMEDIATE).exists(), "the refused write wrote a model"


def test_writing_the_same_conversion_again_removes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = context_for(THREE)
    first = emit(_in_marts(), ctx, out)

    again = emit(_in_marts(), ctx, out)

    assert again.removed == ()
    assert not [d for d in again.decisions if d.key.startswith("emit.removed.")]
    assert sorted(again.paths) == sorted(first.paths)


def test_the_record_lists_exactly_the_files_of_the_latest_write(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = context_for(THREE)
    emit(_in_marts(), ctx, out)

    result = emit(_in_intermediate(), ctx, out)

    recorded = written_by_last_emit(out)
    assert sorted(recorded) == sorted(p.relative_to(out).as_posix() for p in result.paths)
    assert MART not in recorded and INTERMEDIATE in recorded
    # Bookkeeping, not a file of the conversion: never previewed, never
    # listed as written.
    assert out / WRITE_RECORD_NAME not in result.paths


def test_an_out_dir_with_no_record_has_nothing_recorded(tmp_path: Path) -> None:
    assert written_by_last_emit(tmp_path) == ()


def test_an_unreadable_record_is_refused_rather_than_read_as_empty(tmp_path: Path) -> None:
    """Read as empty, a damaged record would let a stale model stay in place
    and be delivered -- the defect, restored, with nothing said.
    """
    out = tmp_path / "out"
    ctx = context_for(THREE)
    emit(_in_marts(), ctx, out)
    (out / WRITE_RECORD_NAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(StaleOutputError, match=WRITE_RECORD_NAME):
        emit(_in_intermediate(), ctx, out)
    assert (out / MART).is_file()


def test_a_record_naming_a_path_outside_out_dir_is_refused(tmp_path: Path) -> None:
    """The record is a file on disk, and a file on disk can say anything. A
    path that escapes out_dir is never a file this tool wrote there.
    """
    out = tmp_path / "out"
    ctx = context_for(THREE)
    emit(_in_marts(), ctx, out)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n", encoding="utf-8")
    record = json.loads((out / WRITE_RECORD_NAME).read_text(encoding="utf-8"))
    record["files"]["../victim.txt"] = "0" * 64
    (out / WRITE_RECORD_NAME).write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match="victim"):
        emit(_in_intermediate(), ctx, out)
    assert victim.read_text(encoding="utf-8") == "keep\n"
