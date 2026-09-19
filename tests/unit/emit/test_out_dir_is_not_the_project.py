"""emit() refuses to write into the dbt project it was read from.

dbtw's whole contract is that it hands you a copy to read before you change
anything. Every file it writes lands at a *project-relative* path, so an
out_dir that is the project writes this conversion's models and sources file
straight over the project's own -- the declaration this conversion does not
repeat is gone from the real project, immediately, with no copy left to
compare against and a CONVERSION_REPORT.md sitting in there claiming a clean
run.

The refusal used to live in `dbtw.cli.main` and nowhere else, which made it a
property of one front end rather than of writing. `emit` is the only thing in
this package that writes, so it is the only place the invariant cannot be
skipped by a caller that did not know to ask -- and a second front end that
called `emit` directly is exactly what was about to happen. These tests drive
`emit` rather than the CLI for that reason; the CLI's own tests still stand
over the command line's half, and `tests/unit/web/test_write.py` drives the
browser's.

Every project here is a throwaway copy of a real fixture, so a broken guard
damages the copy and never the repository's test data -- and every test
asserts the project is byte-identical afterwards, which is the only assertion
that actually says nothing was written.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dbtw.core.assemble import AssembledModel, ProjectChange
from dbtw.core.context import read_project
from dbtw.core.emit import OutputInsideProjectError, emit

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _change() -> ProjectChange:
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
        sources=(),
        decisions=(),
        pending=(),
        dialect=None,
        project_name="jaffle_shop",
    )


def _victim(tmp_path: Path) -> Path:
    """A throwaway copy of a project that declares a source, so a destroyed
    declaration would be visible in the bytes."""
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "sources_at_root", project)
    return project


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _emit_into(project: Path, out: Path) -> None:
    emit(_change(), read_project(project), out)


def test_emit_refuses_an_out_dir_that_is_the_project_itself(tmp_path: Path) -> None:
    project = _victim(tmp_path)
    before = _snapshot(project)

    with pytest.raises(OutputInsideProjectError, match="refusing to convert into the target"):
        _emit_into(project, project)

    assert _snapshot(project) == before


def test_the_refusal_names_the_project_and_the_files_that_were_at_risk(tmp_path: Path) -> None:
    """A refusal that says only "no" leaves the reader guessing which of their
    two paths was wrong. It names the directory it resolved to and some of
    what already lives there."""
    project = _victim(tmp_path)

    with pytest.raises(OutputInsideProjectError) as refusal:
        _emit_into(project, project)

    message = str(refusal.value)
    assert str(project.resolve()) in message, message
    assert "models/sources.yml" in message, message


def test_emit_refuses_an_out_dir_inside_the_project(tmp_path: Path) -> None:
    project = _victim(tmp_path)
    before = _snapshot(project)

    with pytest.raises(OutputInsideProjectError, match="refusing to convert into the target"):
        _emit_into(project, project / "models")

    assert _snapshot(project) == before


def test_emit_refuses_an_out_dir_that_does_not_exist_yet_inside_the_project(
    tmp_path: Path,
) -> None:
    """`mkdir -p` would put it inside the project, so the directory it would
    be created in is what places it. Asserting the directory was not created
    is the half that says the refusal came before the write and not after."""
    project = _victim(tmp_path)
    before = _snapshot(project)

    with pytest.raises(OutputInsideProjectError, match="refusing to convert into the target"):
        _emit_into(project, project / "out" / "here")

    assert not (project / "out").exists(), "the refusal created a directory in the project"
    assert _snapshot(project) == before


def test_emit_refuses_an_out_dir_that_reaches_the_project_through_a_symlink(
    tmp_path: Path,
) -> None:
    """Two paths, one directory. A guard comparing path spelling sees two
    projects and writes into the real one."""
    project = _victim(tmp_path)
    before = _snapshot(project)
    link = tmp_path / "link"
    link.symlink_to(project, target_is_directory=True)

    with pytest.raises(OutputInsideProjectError, match="refusing to convert into the target"):
        _emit_into(project, link)

    assert _snapshot(project) == before


def test_emit_refuses_an_out_dir_whose_models_are_the_projects_models(tmp_path: Path) -> None:
    """A project whose models/ is a symlink into a shared tree is neither the
    root nor under it when out_dir is that shared tree -- and yet every model
    this run writes lands in the project's real models directory."""
    project = _victim(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    real_models = tmp_path / "real_models"
    shutil.move(str(project / "models"), real_models)
    (project / "models").symlink_to(real_models, target_is_directory=True)
    (shared / "models").symlink_to(real_models, target_is_directory=True)
    before = _snapshot(project)

    with pytest.raises(OutputInsideProjectError, match="refusing to convert into the target"):
        _emit_into(project, shared)

    assert _snapshot(project) == before


def test_a_directory_beside_the_project_is_still_a_valid_out(tmp_path: Path) -> None:
    """The refusal has to be narrow or it refuses the ordinary case. This is
    the ordinary case, and it is what makes every assertion above mean
    something."""
    project = _victim(tmp_path)
    before = _snapshot(project)

    _emit_into(project, tmp_path / "out")

    assert (tmp_path / "out" / "models" / "staging" / "stg_orders.sql").is_file()
    assert _snapshot(project) == before


def test_the_projects_own_parent_is_still_a_valid_out(tmp_path: Path) -> None:
    """The project sits inside out_dir here, at a path this run writes nothing
    to. Refusing would be a guard inventing a clash."""
    project = _victim(tmp_path)
    before = _snapshot(project)

    _emit_into(project, tmp_path)

    assert (tmp_path / "models" / "staging" / "stg_orders.sql").is_file()
    assert _snapshot(project) == before
