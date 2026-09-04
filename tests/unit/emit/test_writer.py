from pathlib import Path

import pytest

from dbtw.core.assemble import AssembledModel, ProjectChange, SourceEntry
from dbtw.core.context import read_project
from dbtw.core.emit import emit

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _change(sources=()) -> ProjectChange:
    return ProjectChange(
        models=(
            AssembledModel(
                name="stg_orders",
                path="models/staging/stg_orders.sql",
                body="SELECT 1 AS a",
                materialization="table",
                grants=(),
                layer="staging",
                depends_on=(),
                leading_comments=(),
                source_indices=(0,),
            ),
        ),
        sources=sources,
        decisions=(),
        pending=(),
        dialect=None,
        project_name="jaffle_shop",
    )


def test_writes_model_report_and_creates_directories(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    written = emit(_change(), ctx, tmp_path)
    model_file = tmp_path / "models" / "staging" / "stg_orders.sql"
    assert model_file.is_file()
    assert "materialized='table'" in model_file.read_text()
    assert (tmp_path / "CONVERSION_REPORT.md").is_file()
    assert model_file in written and (tmp_path / "CONVERSION_REPORT.md") in written


def test_sources_go_beside_the_projects_existing_source_file(tmp_path):
    """with_sources declares sources at models/staging/sources.yml, so ours land there."""
    ctx = read_project(FIXTURES / "with_sources")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),)),
        ctx,
        tmp_path,
    )
    sources_file = tmp_path / "models" / "staging" / "sources.yml"
    assert sources_file.is_file()
    assert "name: raw" in sources_file.read_text()


def test_sources_fall_back_to_the_staging_layer_when_the_project_declares_none(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")  # declares no sources at all
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "staging" / "sources.yml").is_file()


def test_no_sources_file_when_there_are_no_sources(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    emit(_change(), ctx, tmp_path)
    assert not (tmp_path / "models" / "staging" / "sources.yml").exists()


def test_nothing_is_written_outside_out_dir(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    written = emit(_change(), ctx, tmp_path)
    assert all(tmp_path in p.parents for p in written)


def test_emit_refuses_to_write_outside_out_dir(tmp_path):
    """pathlib silently drops the left operand when the right is absolute
    (Path('/tmp/out') / '/etc/passwd' -> '/etc/passwd'). The escape target
    here is a sibling of out_dir inside the test's own tmp tree, not a real
    system path — same vulnerability class (an absolute path escaping
    out_dir), without any risk of this test ever touching a file outside
    its own sandbox, even while it's RED.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")
    out_dir = tmp_path / "out"
    escape_target = tmp_path / "escaped.sql"  # sibling of out_dir: outside it
    escaping_model = AssembledModel(
        name="evil",
        path=str(escape_target),
        body="select 1",
        materialization=None,
        grants=(),
        layer="staging",
        depends_on=(),
        leading_comments=(),
        source_indices=(0,),
    )
    change = ProjectChange(
        models=(escaping_model,),
        sources=(),
        decisions=(),
        pending=(),
        dialect=None,
        project_name="jaffle_shop",
    )
    with pytest.raises(ValueError):
        emit(change, ctx, out_dir)
    assert not escape_target.exists()
    assert not out_dir.exists() or not list(out_dir.rglob("*"))


def test_emit_creates_out_dir_when_it_does_not_exist(tmp_path):
    ctx = read_project(FIXTURES / "jaffle_shop")
    out_dir = tmp_path / "does_not_exist_yet"
    empty_change = ProjectChange(
        models=(),
        sources=(),
        decisions=(),
        pending=(),
        dialect=None,
        project_name="jaffle_shop",
    )
    written = emit(empty_change, ctx, out_dir)
    report_file = out_dir / "CONVERSION_REPORT.md"
    assert report_file.is_file()
    assert written == (report_file,)


def test_sources_at_project_root_land_at_the_root_not_in_staging(tmp_path):
    """sources_at_root declares sources at models/sources.yml (the model-path
    root), mirroring the real dbt-labs jaffle_shop shape, and also has a
    staging layer at models/staging. with_sources' existing source file
    happens to already sit inside its staging layer, so it can't tell rule
    (1) — beside the project's existing source file — apart from rule (2) —
    the staging layer's path. This fixture can.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "sources.yml").is_file()
    assert not (tmp_path / "models" / "staging" / "sources.yml").exists()
