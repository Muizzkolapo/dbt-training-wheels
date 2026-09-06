from pathlib import Path

import pytest

from dbtw.core.assemble import AssembledModel, ProjectChange, SourceEntry
from dbtw.core.context import ProjectContext, SourceInfo, read_project
from dbtw.core.emit import DuplicateSourceEntryError, emit

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
    assert model_file in written.paths and (tmp_path / "CONVERSION_REPORT.md") in written.paths


def test_sources_go_beside_the_projects_existing_source_file(tmp_path):
    """with_sources declares sources at models/staging/sources.yml, so ours land
    in that directory — under our own filename, since the project's own file
    already holds that name."""
    ctx = read_project(FIXTURES / "with_sources")
    # A table the project does not already declare: assemble._source_entries
    # never proposes one it does, and _sources_placement asserts that.
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="events"),)),
        ctx,
        tmp_path,
    )
    sources_file = tmp_path / "models" / "staging" / "sources_dbtw.yml"
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
    assert all(tmp_path in p.parents for p in written.paths)


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
    assert written.paths == (report_file,)
    assert written.decisions == ()


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
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "sources_dbtw.yml").is_file()
    staging = tmp_path / "models" / "staging"
    assert not (staging / "sources.yml").exists()
    assert not (staging / "sources_dbtw.yml").exists()


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


def test_our_sources_file_never_lands_on_the_projects_own(tmp_path):
    """sources_at_root declares raw.orders at models/sources.yml, which is
    where emit would otherwise put ours. Nothing this tool writes may destroy
    a declaration, so ours goes under its own name and out_dir carries no file
    at the project's path at all — copy the whole thing across and raw.orders
    survives.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "sources_dbtw.yml").is_file()
    assert not (tmp_path / "models" / "sources.yml").exists()
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "models/sources.yml" in report
    assert "models/sources_dbtw.yml" in report
    assert "raw.orders" in report


def test_the_placement_decision_says_both_files_stand(tmp_path):
    """dbt's duplicate check is per (source, table), not per source name: two
    files declaring source raw with different tables parse clean. The Decision
    has to say that, and offer merging as a preference — claiming dbt rejects
    the pair would send a reader hand-editing YAML to fix nothing.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "dbt reads both" in report
    assert "if you would rather keep one file" in report
    assert "declared twice" not in report
    assert "dbt rejects" not in report


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
    assert "sources_dbtw" not in report
    assert (tmp_path / "models" / "staging" / "sources.yml").is_file()


def test_no_collision_is_claimed_when_no_sources_file_is_written(tmp_path):
    """sources_at_root does declare sources, but this change proposes none —
    no sources.yml is written, so nothing can land on anything.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    emit(_change(), ctx, tmp_path)
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "sources_dbtw" not in report


def test_a_source_name_declared_in_another_file_is_not_a_collision(tmp_path):
    """The project declares source raw in models/schema.yml, so our file lands
    beside it under the ordinary name. Two files declaring source raw with
    different tables is what dbt accepts, and `_source_entries` has already
    skipped every table the project declares — so there is nothing here to
    report, and a Decision would be a warning about a non-problem.
    """
    ctx = _context_declaring(("raw", "orders", "models/schema.yml"))
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "sources.yml").is_file()
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "models/schema.yml" not in report
    assert "sources_dbtw" not in report


def test_every_declaration_the_projects_file_holds_is_named(tmp_path):
    """with_sources declares raw.customers and raw.orders in the file ours
    would have landed on. A reader deciding whether to merge the two files by
    hand needs both, not one of them.
    """
    ctx = read_project(FIXTURES / "with_sources")
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="events"),)),
        ctx,
        tmp_path,
    )
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "raw.customers and raw.orders" in report


def test_the_landing_name_turns_on_the_path_not_on_the_source_names(tmp_path):
    """The project declares source `legacy` in the file ours would land on,
    and we declare `raw` — no source name in common at all. The file is still
    the project's, so ours still goes under its own name: what must not be
    replaced is the file, whatever it happens to declare.
    """
    ctx = _context_declaring(("legacy", "orders", "models/sources.yml"))
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    assert (tmp_path / "models" / "sources_dbtw.yml").is_file()
    assert not (tmp_path / "models" / "sources.yml").exists()
    assert "legacy.orders" in (tmp_path / "CONVERSION_REPORT.md").read_text()


def test_emit_returns_the_placement_decision_it_recorded(tmp_path):
    """The terminal has to be able to say a Decision exists before the user
    runs `cp -r`, and it has to say it in the words the report uses. Returning
    the record itself is what keeps the two from drifting into two different
    explanations of one choice.
    """
    ctx = read_project(FIXTURES / "sources_at_root")
    result = emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        ctx,
        tmp_path,
    )
    (decision,) = result.decisions
    assert "models/sources_dbtw.yml" in decision.action
    assert "models/sources.yml" in decision.action
    assert decision.action in (tmp_path / "CONVERSION_REPORT.md").read_text()


def test_the_landing_name_bumps_past_a_sources_dbtw_the_project_already_has(tmp_path):
    """A previous run's output, copied into the project, leaves a
    sources_dbtw.yml declaring sources of its own. Landing on that is the same
    replacement as landing on sources.yml, one name along — so the name bumps
    until it is one the project does not declare in.
    """
    project = tmp_path / "project"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: p\nconfig-version: 2\n")
    (project / "models" / "a.sql").write_text("select 1 as id")
    (project / "models" / "sources.yml").write_text(
        "version: 2\nsources:\n  - name: raw\n    tables:\n"
        "      - name: orders\n      - name: refunds\n"
    )
    (project / "models" / "sources_dbtw.yml").write_text(
        "version: 2\nsources:\n  - name: raw\n    tables:\n      - name: shipments\n"
    )
    out = tmp_path / "out"
    emit(
        _change(sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),)),
        read_project(project),
        out,
    )
    assert (out / "models" / "sources_dbtw_2.yml").is_file()
    assert not (out / "models" / "sources.yml").exists()
    assert not (out / "models" / "sources_dbtw.yml").exists()


def test_emit_refuses_a_source_entry_the_project_already_declares(tmp_path):
    """`assemble._source_entries` skips every (source, table) the project
    declares, so this state cannot come out of the pipeline. If it ever does,
    the placement Decision would tell the user both files stand side by side —
    beside a file that repeats one of the project's tables, which is the one
    duplicate dbt really rejects. An assert would say the same thing and then
    vanish under `python -O`, leaving the self-contradicting Decision written
    to disk; a raise says it in every build.
    """
    ctx = read_project(FIXTURES / "sources_at_root")  # declares raw.orders
    with pytest.raises(DuplicateSourceEntryError, match="raw.orders"):
        emit(
            _change(sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),)),
            ctx,
            tmp_path,
        )
    assert list(tmp_path.rglob("*")) == []  # refused before anything was written
