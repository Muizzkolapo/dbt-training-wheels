from pathlib import Path

import pytest

from dbtw.core.assemble import AssembledModel, ProjectChange, SourceEntry
from dbtw.core.context import ProjectContext, SourceInfo, read_project
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


def _context_declaring(*declarations: tuple[str, str, str]) -> ProjectContext:
    """A minimal target project whose only interesting feature is where it
    declares sources. Hand-built rather than read off a fixture because the
    case it exists for — a project that declares sources in a file *not*
    named sources.yml — has no fixture, and the collision it causes is a
    property of ProjectContext alone.
    """
    return ProjectContext(
        project_name="handbuilt",
        model_paths=("models",),
        layers=(),
        existing_models=(),
        existing_sources=tuple(
            SourceInfo(source_name=name, table=table, declared_in=where)
            for name, table, where in declarations
        ),
        vars_declared=(),
        detections=(),
    )


def test_report_names_the_source_file_our_sources_yml_would_replace(tmp_path):
    """sources_at_root declares raw.orders at models/sources.yml, which is
    exactly where emit puts ours. Copying the output over the project
    replaces that file and raw.orders stops being declared, so the report
    must name both the file and the declaration that goes with it.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "models/sources.yml" in report
    assert "raw.orders" in report


def test_the_collision_decision_says_what_to_do_about_it(tmp_path):
    """Naming the clash is not enough on its own — a reader has to be able to
    act on it without reading dbtw's source. The Decision has to say what
    replacing the file costs, and that keeping both declares one source twice.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "replaces that file" in report
    assert "declared twice" in report


def test_no_collision_is_claimed_when_the_project_declares_no_sources(tmp_path):
    """jaffle_shop declares no sources at all, so ours lands on nothing. A
    Decision here would be an invented clash.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "already declares sources" not in report
    assert "declared twice" not in report


def test_no_collision_is_claimed_when_no_sources_file_is_written(tmp_path):
    """sources_at_root does declare sources, but this change proposes none —
    no sources.yml is written, so nothing can land on anything.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(_change(), ctx, tmp_path)
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "already declares sources" not in report
    assert "declared twice" not in report


def test_report_names_a_source_the_project_declares_in_another_file(tmp_path):
    """The sibling of the overwrite: the project declares source raw in
    models/schema.yml, so our sources.yml lands beside it rather than on it.
    Nothing is overwritten and nothing is lost — but source raw is now
    declared in two files of one project, which dbt rejects, and that is just
    as silent as the overwrite was.
    """
    ctx = _context_declaring(("raw", "orders", "models/schema.yml"))
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "sources.yml").is_file()
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "models/schema.yml" in report
    assert "declared twice" in report


def test_a_source_the_project_declares_nowhere_is_not_called_a_duplicate(tmp_path):
    """Only a shared source *name* duplicates. The project declares source
    `legacy`; we declare `raw`, in a different file — two names, no clash.
    """
    ctx = _context_declaring(("legacy", "orders", "models/schema.yml"))
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "declared twice" not in report


def test_every_declaration_the_replacement_would_remove_is_named(tmp_path):
    """with_sources declares raw.customers and raw.orders in the file ours
    lands on, and this change declares neither. Naming one of them and
    stopping would leave a reader thinking the other survived.
    """
    ctx = read_project(FIXTURES / "with_sources")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="events"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "raw.customers and raw.orders" in report


def test_a_file_at_our_path_sharing_no_source_name_is_a_rename_not_a_duplicate(tmp_path):
    """The project declares source `legacy` in the file ours lands on, and we
    declare `raw`. Replacing it still costs legacy.orders — but the two
    declare no source in common, so keeping both is a rename, not the
    duplicate dbt rejects, and the Decision must not say otherwise.
    """
    ctx = _context_declaring(("legacy", "orders", "models/sources.yml"))
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "legacy.orders" in report
    assert "different filename" in report
    assert "declared twice" not in report
