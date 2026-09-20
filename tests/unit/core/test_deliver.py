"""Delivery: a reviewed conversion onto a branch of the reader's own project.

Every test here drives a real git repository in a temporary directory. A
mocked `subprocess` would be testing this module against a description of
git rather than against git, and the three refusals are all about states
only git can actually be in -- a repository that is not one, a tree with
changes in it, a branch that already exists.

`emit` refuses to write into the target project, which is what makes the
copy a reader reviews safe. This is the one step that crosses into it, so
what it refuses matters more than what it does.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from tests.unit.assemble.helpers import context_for

from dbtw.core.context import read_project
from dbtw.core.deliver import (
    BranchExistsError,
    DirtyWorkingTreeError,
    NotAGitRepoError,
    deliver,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout


def _repo(tmp_path: Path) -> Path:
    """A copy of the fixture project, as a committed git repository.

    Identity is set on the repository rather than read from the machine, so
    the test does not depend on whoever runs it having git configured.
    """
    import shutil

    root = tmp_path / "project"
    shutil.copytree(FIXTURES / "jaffle_shop", root)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "reader@example.invalid")
    _git(root, "config", "user.name", "The Reader")
    _git(root, "add", "--", "dbt_project.yml")
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts):
        _git(root, "add", "--", str(path.relative_to(root)))
    _git(root, "commit", "-q", "-m", "the project as it was")
    return root


def _conversion(out: Path) -> Path:
    """A written conversion, as `emit` leaves one."""
    (out / "models" / "staging").mkdir(parents=True)
    (out / "models" / "staging" / "stg_events.sql").write_text("select 1\n", encoding="utf-8")
    (out / "models" / "staging" / "stg_events.yml").write_text("version: 2\n", encoding="utf-8")
    (out / "CONVERSION_REPORT.md").write_text("# what happened\n", encoding="utf-8")
    return out


def test_a_delivery_lands_the_files_on_a_new_branch_and_commits_them(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")
    before = _git(root, "rev-parse", "HEAD").strip()

    result = deliver(out, read_project(root), branch="dbtw/events", message="convert events")

    assert result.branch == "dbtw/events"
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "dbtw/events"
    assert (root / "models" / "staging" / "stg_events.sql").read_text() == "select 1\n"
    assert (root / "CONVERSION_REPORT.md").read_text() == "# what happened\n"
    assert result.commit != before
    assert _git(root, "log", "-1", "--pretty=%s").strip() == "convert events"


def test_the_commit_holds_exactly_the_files_the_delivery_reports(tmp_path: Path) -> None:
    """What landed in the commit is what `Delivery.files` says landed.

    Not a test of *how* they were staged. A mutation swapping the by-name
    `git add` for `git add -A` passes this and every other test here, and
    honestly so: the dirty-tree refusal means there is nothing else in the
    tree to sweep up, so under this module's own preconditions the two stage
    the same set. The by-name add is defence in depth for the window between
    that check and the add, and the source says so rather than this test
    pretending to prove it.
    """
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")

    result = deliver(out, read_project(root), branch="dbtw/events", message="convert events")

    committed = _git(root, "show", "--name-only", "--pretty=format:", "HEAD").split()
    assert sorted(committed) == sorted(result.files)
    assert sorted(result.files) == [
        "CONVERSION_REPORT.md",
        "models/staging/stg_events.sql",
        "models/staging/stg_events.yml",
    ]


def test_the_report_is_delivered_last_so_a_reviewer_reads_the_models_first(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")

    result = deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert result.files[-1] == "CONVERSION_REPORT.md"


def test_the_working_tree_is_left_clean(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")

    deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert _git(root, "status", "--porcelain").strip() == ""


def test_a_project_that_is_not_a_git_repository_is_refused(tmp_path: Path) -> None:
    """Without a branch this would be an overwrite in place, which is the one
    thing every other refusal in this tool exists to prevent.
    """
    import shutil

    root = tmp_path / "project"
    shutil.copytree(FIXTURES / "jaffle_shop", root)
    out = _conversion(tmp_path / "out")
    before = sorted(p.name for p in root.rglob("*"))

    with pytest.raises(NotAGitRepoError, match="not a git repository"):
        deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert sorted(p.name for p in root.rglob("*")) == before


def test_a_project_inside_a_repository_rather_than_its_root_is_refused(tmp_path: Path) -> None:
    """A dbt project in a subdirectory of a monorepo would have this branch
    and this commit apply to the monorepo.
    """
    import shutil

    outer = tmp_path / "monorepo"
    outer.mkdir()
    _git(outer, "init", "-q")
    _git(outer, "config", "user.email", "reader@example.invalid")
    _git(outer, "config", "user.name", "The Reader")
    inner = outer / "warehouse"
    shutil.copytree(FIXTURES / "jaffle_shop", inner)
    out = _conversion(tmp_path / "out")

    with pytest.raises(NotAGitRepoError, match="rather than being its root"):
        deliver(out, read_project(inner), branch="dbtw/events", message="m")


def test_uncommitted_changes_are_refused_and_left_alone(tmp_path: Path) -> None:
    """`git checkout -b` carries them onto the new branch, so the commit this
    makes would hold work nobody in this walk has seen.
    """
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")
    theirs = root / "dbt_project.yml"
    theirs.write_text(theirs.read_text() + "\n# a change in progress\n", encoding="utf-8")

    with pytest.raises(DirtyWorkingTreeError, match="uncommitted changes"):
        deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert "# a change in progress" in theirs.read_text(), "their edit was touched"
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() != "dbtw/events"


def test_a_branch_that_already_exists_is_refused_before_anything_is_copied(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")
    _git(root, "branch", "dbtw/events")

    with pytest.raises(BranchExistsError, match="already has a branch"):
        deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert not (root / "models" / "staging" / "stg_events.sql").exists()
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() != "dbtw/events"


def test_an_empty_output_directory_is_refused(tmp_path: Path) -> None:
    """Delivering nothing would make a branch and an empty commit that claim
    a conversion happened.
    """
    root = _repo(tmp_path)
    empty = tmp_path / "out"
    empty.mkdir()

    with pytest.raises(ValueError, match="nothing to deliver"):
        deliver(empty, read_project(root), branch="dbtw/events", message="m")

    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() != "dbtw/events"


def test_delivery_replaces_a_file_the_project_already_has(tmp_path: Path) -> None:
    """The branch is what makes this safe rather than a refusal: a reader can
    read the diff, and `git checkout` undoes it. What must not happen is the
    replacement going unrecorded, so the file is in the commit.
    """
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")
    existing = root / "models" / "staging" / "stg_events.sql"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("-- theirs\n", encoding="utf-8")
    _git(root, "add", "--", "models/staging/stg_events.sql")
    _git(root, "commit", "-q", "-m", "a model they already had")

    result = deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert existing.read_text() == "select 1\n"
    assert "models/staging/stg_events.sql" in result.files
    committed = _git(root, "show", "--name-only", "--pretty=format:", "HEAD").split()
    assert "models/staging/stg_events.sql" in committed


def test_the_delivery_is_authored_by_whoever_runs_it(tmp_path: Path) -> None:
    """No author override anywhere in this module: the reader's own git
    configuration applies, so the commit is theirs and their signing setup
    is what signs it.
    """
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")

    deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert _git(root, "log", "-1", "--pretty=%an").strip() == "The Reader"
    assert _git(root, "log", "-1", "--pretty=%ae").strip() == "reader@example.invalid"


def test_a_conversion_can_be_delivered_end_to_end_from_the_real_pipeline(
    tmp_path: Path,
) -> None:
    """The whole path in one test: convert real SQL against a real project,
    emit it outside that project, then deliver it into it on a branch.
    """
    from tests.unit.assemble.helpers import convert

    from dbtw.core.emit import emit

    root = _repo(tmp_path)
    out = tmp_path / "out"
    change = convert("INSERT INTO revenue_events SELECT order_id FROM raw.orders;\n")
    emit(change, context_for("jaffle_shop"), out)

    result = deliver(out, read_project(root), branch="dbtw/revenue", message="convert revenue")

    assert result.files, "the conversion delivered nothing"
    for relative in result.files:
        assert (root / relative).is_file(), f"{relative} did not land"
    assert _git(root, "status", "--porcelain").strip() == ""
