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

import hashlib
import json
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
from dbtw.core.emit import WRITE_RECORD_NAME

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
    """A written conversion, as `emit` leaves one: the files, and the record
    of which files the write produced. Delivery reads the record, not the
    directory, so a conversion without one is not a conversion it delivers.
    """
    files = {
        "models/staging/stg_events.sql": "select 1\n",
        "models/staging/stg_events.yml": "version: 2\n",
        "CONVERSION_REPORT.md": "# what happened\n",
    }
    for relative, text in files.items():
        (out / relative).parent.mkdir(parents=True, exist_ok=True)
        (out / relative).write_text(text, encoding="utf-8")
    record = {
        "format": 1,
        "files": {
            relative: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for relative, text in files.items()
        },
    }
    (out / WRITE_RECORD_NAME).write_text(json.dumps(record), encoding="utf-8")
    return out


def _look_at(root: Path, branch: str) -> None:
    """Check out `branch`, as a reader does to read what a delivery put there.

    `deliver` checks out the reader's own branch again after its commit, so
    the delivered files are on the branch and not in the working tree.
    """
    _git(root, "checkout", "-q", branch)


def test_a_delivery_lands_the_files_on_a_new_branch_and_commits_them(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")
    before = _git(root, "rev-parse", "HEAD").strip()
    start = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    result = deliver(out, read_project(root), branch="dbtw/events", message="convert events")

    assert result.branch == "dbtw/events"
    assert _git(root, "rev-parse", "dbtw/events").strip() == result.commit
    assert _git(root, "rev-parse", "dbtw/events~1").strip() == before
    _look_at(root, result.branch)
    assert (root / "models" / "staging" / "stg_events.sql").read_text() == "select 1\n"
    assert (root / "CONVERSION_REPORT.md").read_text() == "# what happened\n"
    assert result.commit != before
    assert _git(root, "log", "-1", "--pretty=%s").strip() == "convert events"
    assert start != "dbtw/events"


def test_a_delivery_leaves_the_project_on_the_branch_it_was_on(tmp_path: Path) -> None:
    """The walk reads the project on every run. Left on the new branch, the
    project held the conversion's own files, so every later screen described
    a project that already contained the conversion, and a second write was
    built against it.
    """
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")
    start = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    before = _git(root, "rev-parse", "HEAD").strip()

    deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == start
    assert _git(root, "rev-parse", "HEAD").strip() == before
    assert not (root / "models" / "staging" / "stg_events.sql").exists()
    assert _git(root, "status", "--porcelain").strip() == ""


def test_a_delivery_from_a_detached_head_comes_back_to_that_commit(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")
    before = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "checkout", "-q", "--detach")

    deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"
    assert _git(root, "rev-parse", "HEAD").strip() == before


def test_a_free_branch_name_counts_up_past_the_ones_that_exist(tmp_path: Path) -> None:
    from dbtw.core.deliver import free_branch

    root = _repo(tmp_path)
    assert free_branch(root, "dbtw/events") == "dbtw/events"
    _git(root, "branch", "dbtw/events")
    assert free_branch(root, "dbtw/events") == "dbtw/events-2"
    _git(root, "branch", "dbtw/events-2")
    assert free_branch(root, "dbtw/events") == "dbtw/events-3"


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

    committed = _git(root, "show", "--name-only", "--pretty=format:", result.branch).split()
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

    assert existing.read_text() == "-- theirs\n", "the reader's branch keeps their file"
    _look_at(root, result.branch)
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

    assert _git(root, "log", "-1", "--pretty=%an", "dbtw/events").strip() == "The Reader"
    assert (
        _git(root, "log", "-1", "--pretty=%ae", "dbtw/events").strip() == "reader@example.invalid"
    )


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
    assert _git(root, "status", "--porcelain").strip() == ""
    _look_at(root, result.branch)
    for relative in result.files:
        assert (root / relative).is_file(), f"{relative} did not land"
    assert _git(root, "status", "--porcelain").strip() == ""


# --- what a delivery carries: the latest write, and nothing else in out_dir

_REWRITE_CHAIN = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders;\n"
    "INSERT INTO revenue_daily SELECT order_id, amount FROM revenue_events;\n"
)


def _three_layers_repo(tmp_path: Path) -> Path:
    import shutil

    root = tmp_path / "project"
    shutil.copytree(FIXTURES / "three_layers", root)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "reader@example.invalid")
    _git(root, "config", "user.name", "The Reader")
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts):
        _git(root, "add", "--", str(path.relative_to(root)))
    _git(root, "commit", "-q", "-m", "the project as it was")
    return root


def test_a_delivery_after_a_rewrite_carries_the_moved_model_once(tmp_path: Path) -> None:
    """Written with the model in marts, then again with it in intermediate:
    the branch holds the model in intermediate and nowhere else. Two copies
    would be two models with one name, which dbt refuses to parse.
    """
    from tests.unit.assemble.helpers import convert

    from dbtw.core.emit import emit
    from dbtw.core.passes import answer_for

    root = _three_layers_repo(tmp_path)
    ctx = read_project(root)
    out = tmp_path / "out"
    pristine = convert(_REWRITE_CHAIN, project="three_layers")
    emit(pristine, ctx, out)
    question = next(d for d in pristine.decisions if d.key.startswith("assemble.layer."))
    moved = convert(
        _REWRITE_CHAIN,
        project="three_layers",
        answers={question.key: answer_for(question, "intermediate")},
    )
    emit(moved, ctx, out)

    result = deliver(out, ctx, branch="dbtw/revenue", message="convert revenue")

    assert "models/intermediate/revenue_daily.sql" in result.files
    assert "models/marts/revenue_daily.sql" not in result.files
    _look_at(root, result.branch)
    assert not (root / "models" / "marts" / "revenue_daily.sql").exists()
    committed = _git(root, "show", "--name-only", "--pretty=format:", "HEAD").split()
    assert sorted(committed) == sorted(result.files)


def test_a_file_of_the_readers_own_in_out_dir_is_not_delivered(tmp_path: Path) -> None:
    """The out_dir can hold the reader's own files. Delivery copies what the
    write produced, so a scratch note beside the conversion stays where the
    reader left it rather than being committed into their project.
    """
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")
    (out / "scratch.sql").write_text("select 'mine'\n", encoding="utf-8")

    result = deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert "scratch.sql" not in result.files
    _look_at(root, result.branch)
    assert not (root / "scratch.sql").exists()
    assert WRITE_RECORD_NAME not in result.files
    assert not (root / WRITE_RECORD_NAME).exists()


def test_a_recorded_file_that_is_gone_is_refused_before_anything_is_copied(
    tmp_path: Path,
) -> None:
    """Delivering the rest would put part of a conversion on a branch that
    claims all of it.
    """
    root = _repo(tmp_path)
    out = _conversion(tmp_path / "out")
    (out / "models" / "staging" / "stg_events.yml").unlink()

    with pytest.raises(ValueError, match="stg_events.yml"):
        deliver(out, read_project(root), branch="dbtw/events", message="m")

    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() != "dbtw/events"
