"""Taking a conversion the reader has already seen into their own project.

This is the one part of dbtw that writes into the target dbt project, and it
is deliberately the last thing that happens rather than part of `emit`.
`emit` writes to a directory outside the project and refuses to write inside
one (`emit.refuse_output_inside_project`), so what a reader reviews on the
files screen is a copy that cannot have damaged anything. Delivery is the
separate, explicit step that moves that reviewed copy across -- and it does
it on a branch of the reader's own git repository, so the move is a thing
they can read, revert, and open a pull request from.

Three refusals, all before anything is copied, all about the reader's
repository rather than about their SQL:

* the project is not a git repository, or is not the root of one. A branch
  is the whole safety story here -- without one this would be an
  unreviewable overwrite of files in place;
* the working tree has changes. A commit made over someone's uncommitted
  work mixes their edit into a commit that claims to be this conversion, and
  `git checkout -b` would carry those changes onto the new branch where they
  do not belong;
* the branch already exists. Reusing it would add a second conversion's
  files to a branch whose name says it holds one.

What this module does not do: push, open a pull request, or talk to a
forge. Those need a remote, credentials, and a host's API, and each is a
decision about someone's account rather than about their files. The branch
is local, the commit is theirs -- `git commit` with no author override, so
their own name and signing configuration apply -- and pushing it is a thing
they do when they have read it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from dbtw.core.context import ProjectContext

# The report is delivered with the models rather than left behind in the
# output directory. It is the review narrative for exactly this change --
# every Decision, in the engine's own words -- and a pull request whose
# reasoning sits in a directory nobody pushed is a pull request a reviewer
# reads without it.
_ALWAYS_DELIVERED = "CONVERSION_REPORT.md"


class NotAGitRepoError(ValueError):
    """The target project is not the root of a git repository.

    Input-driven, and a usage error: the reader pointed at a directory, and
    what is wrong is the directory rather than this tool. Delivery has no
    safe shape without a branch to put the change on.
    """


class DirtyWorkingTreeError(ValueError):
    """The target project has uncommitted changes.

    Refused rather than worked around. `git checkout -b` carries uncommitted
    changes onto the new branch, so a commit made here would hold both this
    conversion and whatever the reader had in progress, under a message that
    claims only the first.
    """


class BranchExistsError(ValueError):
    """The branch this delivery would create is already there.

    A branch named for a conversion holds one. Adding a second one's files
    to it makes a branch whose name describes half of what it carries.
    """


class GitFailedError(RuntimeError):
    """A git command this module ran failed.

    Not a usage error: every git call here is made after its own precondition
    has been checked, so a failure is either a repository in a state this
    module does not understand or a bug in it. It carries git's own stderr,
    because git says what went wrong better than a paraphrase would.
    """


@dataclass(frozen=True, slots=True)
class Delivery:
    """What a delivery did: where it went, what it carried, and its commit.

    `files` are project-relative paths, in the order they were copied -- the
    same strings the files screen showed, so a reader can hold what landed
    against what they were told would land.
    """

    branch: str
    files: tuple[str, ...]
    commit: str


def deliver(out_dir: Path, ctx: ProjectContext, *, branch: str, message: str) -> Delivery:
    """Copy `out_dir` into the project on a new branch, and commit it.

    Every refusal fires before the first file is copied. A delivery that
    stopped half way would leave the reader's project holding some of a
    conversion, on a branch created for it, with nothing saying which half --
    which is worse than not starting.
    """
    root = ctx.root
    _refuse_unless_git_root(root)
    _refuse_if_dirty(root)
    _refuse_if_branch_exists(root, branch)

    files = _deliverable(out_dir)
    if not files:
        raise ValueError(
            f"nothing to deliver: {out_dir} holds no files. Write the conversion "
            "before delivering it"
        )

    _git(root, "checkout", "-b", branch)
    for relative in files:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((out_dir / relative).read_bytes())
    # Added by name rather than `git add -A`. The dirty-tree refusal above
    # means there is nothing else in the tree to sweep up, so under this
    # module's own preconditions the two stage the same set -- a mutation
    # swapping them passes every test here, and that is recorded rather than
    # papered over. What this buys is the window between that check and this
    # line: a file appearing in the reader's project while the copy runs is
    # theirs, and naming what we stage is what keeps it out of a commit that
    # claims to hold one conversion.
    _git(root, "add", "--", *files)
    _git(root, "commit", "-m", message)
    commit = _git(root, "rev-parse", "HEAD").strip()
    return Delivery(branch=branch, files=files, commit=commit)


def _deliverable(out_dir: Path) -> tuple[str, ...]:
    """Every file in `out_dir`, project-relative, report last.

    Sorted, so a delivery's file list does not depend on the order a
    filesystem happened to walk a directory in, with the report at the end
    because it is the one file that is about the others.
    """
    found = sorted(
        path.relative_to(out_dir).as_posix() for path in out_dir.rglob("*") if path.is_file()
    )
    models = [name for name in found if name != _ALWAYS_DELIVERED]
    report = [name for name in found if name == _ALWAYS_DELIVERED]
    return tuple(models + report)


def _refuse_unless_git_root(root: Path) -> None:
    """Refuse a project that is not the root of a git repository.

    The *root*, not merely inside one: a dbt project sitting in a
    subdirectory of some larger repository would have its branch, its commit
    and its refusals apply to that larger repository instead, which is a
    reader's monorepo being committed to by a tool they pointed at one
    project inside it.
    """
    try:
        top = _git(root, "rev-parse", "--show-toplevel").strip()
    except GitFailedError as exc:
        raise NotAGitRepoError(
            f"{root} is not a git repository, so there is nowhere safe to put this "
            "conversion: delivery makes a branch, and without one this would "
            f"overwrite files in place. git said: {exc}"
        ) from exc
    if not Path(top).samefile(root):
        raise NotAGitRepoError(
            f"{root} is inside the git repository at {top} rather than being its root. "
            "Delivering here would branch and commit that repository, which is not the "
            "project this conversion was read against"
        )


def _refuse_if_dirty(root: Path) -> None:
    changed = _git(root, "status", "--porcelain").strip()
    if changed:
        raise DirtyWorkingTreeError(
            f"{root} has uncommitted changes, and a new branch would carry them onto "
            "itself: the commit this makes would hold both this conversion and work "
            f"nobody here has seen. Commit or stash them first. git reports:\n{changed}"
        )


def _refuse_if_branch_exists(root: Path, branch: str) -> None:
    existing = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=root,
        capture_output=True,
    )
    if existing.returncode == 0:
        raise BranchExistsError(
            f"{root} already has a branch named {branch!r}. A branch named for a "
            "conversion holds one; delivering a second onto it would make its name "
            "describe half of what it carries"
        )


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitFailedError(f"git {' '.join(arguments)} failed in {root}: {result.stderr.strip()}")
    return result.stdout
