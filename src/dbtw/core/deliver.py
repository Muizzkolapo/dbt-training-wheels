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

After the commit, `deliver` checks out the branch the project was on before.
The new branch holds the conversion, and the project stays as the reader had
it. A walk that changes an answer and writes again then builds that write
against the project the reader has, and its delivery starts a new branch
from the same place as the first one.

`deliver` stops at the local branch, and `push` is a second function behind
a second button for a reason. Delivery writes inside a directory the reader
already handed this tool; a push leaves their machine and is the first thing
here anybody else can see. One button doing both would be one press away
from publishing a conversion nobody had read.

What neither does is open the pull request. That needs a forge's API and a
token -- a decision about someone's account rather than about their files.
`push` hands back whatever the remote printed instead, which for the common
forges is the link that opens one.

The commit is theirs throughout: `git commit` with no author override, so
their own name and signing configuration apply, and `git push` with their
own remote and credentials, so what this can send is exactly what they could
send from a terminal in that directory.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from dbtw.core.context import ProjectContext
from dbtw.core.emit import REPORT_NAME, written_by_last_emit

# The report is delivered with the models rather than left behind in the
# output directory. It is the review narrative for exactly this change --
# every Decision, in the engine's own words -- and a pull request whose
# reasoning sits in a directory nobody pushed is a pull request a reviewer
# reads without it.
_ALWAYS_DELIVERED = REPORT_NAME


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


class NoRemoteError(ValueError):
    """The project has no remote to push to.

    Input-driven, and a usage error about the reader's repository rather than
    about their SQL: a repository with no remote is a perfectly good
    repository, and the branch this walk made is still there. What is missing
    is somewhere to send it, and only they can say where.
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
    """Copy the latest write in `out_dir` into the project on a new branch,
    and commit it. Only the files that write produced: see `_deliverable`.

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
            f"nothing to deliver. {out_dir} contains no conversion that this tool wrote. "
            "Write the conversion before you deliver it"
        )

    # Where the reader was, so the project goes back there after the commit.
    # Left on the new branch, the project holds this conversion's own files,
    # and the walk -- which reads the project on every run -- then describes
    # a project that already contains the conversion: every model "already
    # exists", and a second write, made after a changed answer, is built
    # against that project and not against the one the reader has.
    start = _current_ref(root)
    _git(root, "checkout", "-q", "-b", branch)
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
    _git(root, "checkout", "-q", start)
    return Delivery(branch=branch, files=files, commit=commit)


def _current_ref(root: Path) -> str:
    """The branch the project has checked out, or its commit when HEAD is
    detached. What `deliver` checks out again after its commit."""
    named = subprocess.run(
        ["git", "symbolic-ref", "-q", "--short", "HEAD"], cwd=root, capture_output=True, text=True
    )
    if named.returncode == 0:
        return named.stdout.strip()
    return _git(root, "rev-parse", "HEAD").strip()


def branch_exists(root: Path, branch: str) -> bool:
    """Whether the repository at `root` has a local branch named `branch`."""
    found = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=root,
        capture_output=True,
    )
    return found.returncode == 0


def free_branch(root: Path, wanted: str) -> str:
    """`wanted`, or `wanted` with the first number that makes it a branch
    name the repository at `root` does not have yet.

    `deliver` refuses a branch that exists, so a second delivery in one walk
    needs a name of its own. The number counts up from 2, because the first
    delivery took the name without a number.
    """
    if not branch_exists(root, wanted):
        return wanted
    number = 2
    while branch_exists(root, f"{wanted}-{number}"):
        number += 1
    return f"{wanted}-{number}"


def earlier_write(branch: str) -> str:
    """What the written screen says about a delivery that a newer write has
    passed: the branch holds the write before this one, not this one."""
    return (
        f"The branch {branch} contains an earlier write. This write is not on a branch. "
        "Put this write on a new branch."
    )


@dataclass(frozen=True, slots=True)
class Push:
    """What a push did: where it went, and what the remote said back.

    `said` is git's own output, verbatim and unparsed. Forges print the
    pull-request link there -- GitHub's "Create a pull request for 'x' on
    GitHub by visiting:", GitLab's equivalent -- and that link is the most
    useful thing on this screen. It is shown rather than read, because the
    line a forge prints is theirs to change and a parser for it here would
    be this tool claiming to know what every remote says.
    """

    remote: str
    branch: str
    said: str


def push(root: Path, *, branch: str, remote: str = "origin") -> Push:
    """Push `branch` to `remote`, and hand back what the remote said.

    Separate from `deliver` and pressed separately, because they are
    different promises. `deliver` writes inside a directory the reader
    already gave this tool; a push leaves their machine, reaches a server
    under their credentials, and is the first thing here that anybody else
    can see. A walk that did both on one button would be one press away from
    publishing a conversion nobody had read.

    No credentials of its own, and no forge API. It runs `git push` with the
    reader's own configuration -- their remote, their keys, their name on the
    commit -- so what this can push is exactly what they could push from a
    terminal in that directory, and nothing this tool knows makes it more.

    It stops at the push. Opening the pull request needs a forge's API and a
    token, which is a decision about someone's account rather than about
    their files; what comes back instead is whatever the remote printed,
    which for the common forges is the link that opens one.
    """
    _refuse_unless_git_root(root)
    _refuse_without_remote(root, remote)
    _refuse_unless_branch_exists(root, branch)
    # `--set-upstream`, so the branch a reader pushed is the branch their next
    # `git status` talks about. Without it they are left on a branch git
    # describes as having no upstream, one command after this tool told them
    # it had been pushed.
    said = _git_output(root, "push", "--set-upstream", remote, branch)
    return Push(remote=remote, branch=branch, said=said.strip())


def _refuse_without_remote(root: Path, remote: str) -> None:
    """Refuse a project with nowhere to send the branch."""
    remotes = _git(root, "remote").split()
    if remote not in remotes:
        listed = ", ".join(sorted(remotes))
        raise NoRemoteError(
            f"{root} has no remote called {remote!r}"
            + (
                f". The repository has these remotes: {listed}"
                if listed
                else ", and no remotes at all"
            )
            + ". The branch is still here. Add a remote and push the branch yourself, "
            "or add a remote and ask for the push again"
        )


def _refuse_unless_branch_exists(root: Path, branch: str) -> None:
    """Refuse to push a branch this repository does not have.

    Unreachable from the walk, which only offers a push for a branch it has
    just made. Raised rather than left to git so the refusal names the
    branch, instead of surfacing git's own message about a refspec.
    """
    if not branch_exists(root, branch):
        raise NoRemoteError(
            f"{root} has no branch named {branch!r} to push. A push follows a "
            "delivery, and this push follows no delivery"
        )


def _deliverable(out_dir: Path) -> tuple[str, ...]:
    """The files the latest write into `out_dir` produced, project-relative,
    report last.

    Read off the record `emit` keeps of its latest write, not off a listing
    of the directory. A listing carried everything the directory held: a
    model an earlier write had put in another layer, which made the branch
    hold two models with one name, and any file of the reader's own that
    they kept beside the conversion. Neither is the conversion they were
    shown on the written screen, which is the list `EmitResult.paths` gave
    and the record holds.

    A recorded file that is gone is refused before anything is copied:
    delivering the rest would put part of a conversion on a branch that
    claims all of it.

    Sorted, so a delivery's file list does not depend on the order a
    write happened to produce files in, with the report at the end because
    it is the one file that is about the others.
    """
    found = sorted(written_by_last_emit(out_dir))
    missing = [name for name in found if not (out_dir / name).is_file()]
    if missing:
        raise ValueError(
            f"this tool cannot deliver {out_dir}. The latest write there produced "
            f"{', '.join(missing)}, and {'it is' if len(missing) == 1 else 'they are'} no "
            "longer there. Write the conversion again before you deliver it"
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
            f"{root} is not a git repository, so this tool has no safe place to put this "
            "conversion. A delivery makes a branch. Without a branch, the delivery would "
            f"overwrite files in place. git said: {exc}"
        ) from exc
    if not Path(top).samefile(root):
        raise NotAGitRepoError(
            f"{root} is inside the git repository at {top} rather than being its root. "
            "A delivery here would make a branch and a commit in that repository. That "
            "repository is not the project that this tool read for this conversion"
        )


def _refuse_if_dirty(root: Path) -> None:
    changed = _git(root, "status", "--porcelain").strip()
    if changed:
        raise DirtyWorkingTreeError(
            f"{root} has uncommitted changes, and a new branch would carry them. Then the "
            "commit that this tool makes would contain this conversion and also work "
            "that nobody here has seen. Commit or stash the changes first. "
            f"git reports:\n{changed}"
        )


def _refuse_if_branch_exists(root: Path, branch: str) -> None:
    if branch_exists(root, branch):
        raise BranchExistsError(
            f"{root} already has a branch named {branch!r}. A branch named for a "
            "conversion contains one conversion. A second delivery onto that branch would "
            "make the branch name describe half of what the branch contains"
        )


def _git_output(root: Path, *arguments: str) -> str:
    """Run git and return everything it said, both streams.

    `git push` reports to stderr even when it succeeds -- the branch summary
    and a forge's pull-request link both arrive there -- so a caller reading
    stdout alone gets an empty string back from a push that worked.
    """
    result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitFailedError(f"git {' '.join(arguments)} failed in {root}: {result.stderr.strip()}")
    return f"{result.stdout}{result.stderr}"


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitFailedError(f"git {' '.join(arguments)} failed in {root}: {result.stderr.strip()}")
    return result.stdout
