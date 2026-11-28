"""The reason for each removed file is true of that kind of file.

Testers found one fixed reason on every removal: "an answer moved the model
to another layer, or the script no longer builds the model". It was given
for a schema .yml whose only test had been dropped, and for a sources file,
which is not a model. A reason that is false of the file teaches the reader
to distrust the report.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.assemble.helpers import context_for
from tests.unit.emit.test_rewrite import (
    DESCRIBED,
    MART,
    MART_YML,
    THREE,
    _in_intermediate,
    _in_marts,
)
from tests.unit.emit.test_writer import _change
from tests.unit.test_ste_wording import findings

from dbtw.core.assemble import SourceEntry
from dbtw.core.context import read_project
from dbtw.core.emit import emit
from dbtw.core.passes import Decision

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _reasons(result_decisions: tuple[Decision, ...]) -> dict[str, str]:
    prefix = "emit.removed."
    return {
        d.key.removeprefix(prefix): d.reason for d in result_decisions if d.key.startswith(prefix)
    }


def test_a_model_that_moved_layer_is_said_to_have_moved(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = context_for(THREE)
    emit(_in_marts(), ctx, out)

    reasons = _reasons(emit(_in_intermediate(), ctx, out).decisions)

    assert "moved the model to another layer" in reasons[MART]
    assert "moved the model to another layer" in reasons[MART_YML]


def test_a_schema_file_with_no_test_or_description_left_says_so(tmp_path: Path) -> None:
    from tests.unit.assemble.helpers import convert
    from tests.unit.emit.test_rewrite import CHAIN

    out = tmp_path / "out"
    ctx = context_for(THREE)
    emit(convert(CHAIN, project=THREE, descriptions=DESCRIBED), ctx, out)

    reasons = _reasons(emit(convert(CHAIN, project=THREE), ctx, out).decisions)

    assert list(reasons) == [MART_YML], "the model stays, only its .yml goes"
    reason = reasons[MART_YML]
    assert "test" in reason and "description" in reason, reason
    assert "The cause is one of two: an answer moved" not in reason


def test_a_sources_file_is_not_called_a_model(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = read_project(FIXTURES / "jaffle_shop")
    emit(_change(sources=(SourceEntry(source_name="raw", schema="raw", table="x"),)), ctx, out)

    reasons = _reasons(emit(_change(), ctx, out).decisions)

    ((path, reason),) = reasons.items()
    assert path.endswith("sources.yml")
    assert "source" in reason
    assert "model to another layer" not in reason
    assert "builds the model" not in reason


def test_every_removal_reason_follows_the_wording_rules(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ctx = context_for(THREE)
    emit(_in_marts(), ctx, out)
    reasons = _reasons(emit(_in_intermediate(), ctx, out).decisions)

    out2 = tmp_path / "out2"
    jaffle = read_project(FIXTURES / "jaffle_shop")
    emit(_change(sources=(SourceEntry(source_name="raw", schema="raw", table="x"),)), jaffle, out2)
    reasons |= _reasons(emit(_change(), jaffle, out2).decisions)

    for reason in reasons.values():
        assert findings(reason) == [], reason
