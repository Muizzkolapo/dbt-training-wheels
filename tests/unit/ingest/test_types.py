import dataclasses

import pytest

from dbtw.core.ingest import ClassifiedStatement, IngestResult, RawStatement


def test_raw_statement_is_immutable():
    raw = RawStatement(
        source_file="etl/script.sql", index=0, text="SELECT 1", line_start=1, line_end=1
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        raw.text = "SELECT 2"  # type: ignore[misc]


def test_classified_statement_carries_kind_and_reason():
    raw = RawStatement(
        source_file="etl/script.sql", index=0, text="SELECT 1", line_start=1, line_end=1
    )
    stmt = ClassifiedStatement(raw=raw, kind="select", reason="parsed as SELECT")
    assert stmt.kind == "select"
    assert stmt.reason


def test_ingest_result_holds_dialect_and_warnings():
    result = IngestResult(statements=(), dialect=None, warnings=("no dialect specified",))
    assert result.dialect is None
    assert "no dialect" in result.warnings[0]
