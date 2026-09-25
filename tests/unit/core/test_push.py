"""Sending the delivered branch to the reader's own remote.

The design's last step. Everything here is `git push` with the reader's own
configuration -- their remote, their keys, their name -- so what this can send
is exactly what they could send from a terminal in that directory. It stops
at the push: opening the pull request needs a forge's API and a token, which
is a decision about someone's account rather than about their files.

Every test pushes to a bare repository on disk. Nothing here reaches a
network, and a test that needed one would be a test that fails on a train.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dbtw.core.deliver import NoRemoteError, NotAGitRepoError, push


def _git(root: Path, *arguments: str) -> str:
    done = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A project with one commit and a branch to push, and a bare remote."""
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "reader@example.invalid")
    _git(root, "config", "user.name", "The Reader")
    (root / "dbt_project.yml").write_text("name: shop\n", encoding="utf-8")
    _git(root, "add", "--", "dbt_project.yml")
    _git(root, "commit", "-q", "-m", "the project as it was")

    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
    _git(root, "remote", "add", "origin", str(bare))

    _git(root, "checkout", "-q", "-b", "dbtw/stg_orders")
    (root / "models").mkdir()
    (root / "models" / "stg_orders.sql").write_text("SELECT 1\n", encoding="utf-8")
    _git(root, "add", "--", "models/stg_orders.sql")
    _git(root, "commit", "-q", "-m", "Convert 1 model with dbt training wheels")
    return root


def test_the_branch_reaches_the_remote(repo: Path) -> None:
    """The whole promise: the commit the reader read is on their server."""
    here = _git(repo, "rev-parse", "HEAD")

    done = push(repo, branch="dbtw/stg_orders")

    assert done.branch == "dbtw/stg_orders"
    assert done.remote == "origin"
    there = _git(repo, "rev-parse", "refs/remotes/origin/dbtw/stg_orders")
    assert there == here, "the remote does not have the commit that was pushed"


def test_the_pushed_branch_is_the_one_the_readers_next_command_talks_about(repo: Path) -> None:
    """`--set-upstream`, or a reader is left on a branch git describes as
    having no upstream one command after being told it was pushed.
    """
    push(repo, branch="dbtw/stg_orders")

    assert _git(repo, "rev-parse", "--abbrev-ref", "dbtw/stg_orders@{upstream}") == (
        "origin/dbtw/stg_orders"
    )


def test_what_the_remote_said_comes_back_from_both_streams(repo: Path, tmp_path: Path) -> None:
    """A forge prints the pull-request link in its push output, and that link
    is the most useful thing on the screen that shows this.

    It arrives on **stderr**. `git push --set-upstream` splits what it says:
    "branch 'x' set up to track..." goes to stdout, and the push report --
    `To <remote>`, `* [new branch]`, and a forge's "create a pull request"
    link -- goes to stderr. A version of this that read stdout alone still
    said the branch name, so a test asserting only that passed while the link
    this feature exists for was being thrown away. The remote's own path
    appears only in the stderr half, which is what pins it.

    Unparsed, because the line a forge prints is theirs to change: a parser
    here would be this tool claiming to know what every remote says.
    """
    done = push(repo, branch="dbtw/stg_orders")

    assert done.said, "git said nothing at all"
    assert "dbtw/stg_orders" in done.said, "the branch is not named"
    bare = _git(repo, "remote", "get-url", "origin")
    assert bare in done.said, (
        "the push report is missing -- git writes it to stderr, and a forge "
        "writes its pull-request link there too"
    )


def test_a_project_with_no_remote_is_refused_by_name(repo: Path) -> None:
    """The branch is still there. What is missing is somewhere to send it,
    and only the reader can say where."""
    _git(repo, "remote", "remove", "origin")

    with pytest.raises(NoRemoteError) as refused:
        push(repo, branch="dbtw/stg_orders")

    assert "origin" in str(refused.value)
    assert _git(repo, "branch", "--show-current") == "dbtw/stg_orders", "the branch survived"


def test_a_remote_by_another_name_is_named_in_the_refusal(repo: Path) -> None:
    """So a reader whose remote is called `upstream` is told what they do
    have, rather than only what they do not."""
    _git(repo, "remote", "rename", "origin", "upstream")

    with pytest.raises(NoRemoteError) as refused:
        push(repo, branch="dbtw/stg_orders")

    assert "upstream" in str(refused.value)


def test_a_branch_this_repository_does_not_have_is_refused(repo: Path) -> None:
    with pytest.raises(NoRemoteError) as refused:
        push(repo, branch="dbtw/never_made")

    assert "dbtw/never_made" in str(refused.value)


def test_a_directory_that_is_not_a_git_repository_is_refused(tmp_path: Path) -> None:
    somewhere = tmp_path / "not-a-repo"
    somewhere.mkdir()

    with pytest.raises(NotAGitRepoError):
        push(somewhere, branch="dbtw/stg_orders")


def test_pushing_twice_sends_the_same_commit_and_says_so(repo: Path) -> None:
    """A reader who presses it again has not made a second thing. git says
    everything is up to date, and that sentence is what comes back."""
    first = push(repo, branch="dbtw/stg_orders")
    second = push(repo, branch="dbtw/stg_orders")

    assert first.said != "" and second.said != ""
    assert _git(repo, "rev-parse", "refs/remotes/origin/dbtw/stg_orders") == _git(
        repo, "rev-parse", "HEAD"
    )
