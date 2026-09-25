"""The button that sends the branch to the reader's own remote.

The last thing this walk does, and the first thing in it anybody else can
see. Delivery writes inside a directory the reader already handed this tool;
a push leaves their machine. They are two buttons for that reason, and this
is what holds them apart.

The remote is a bare repository on disk. Nothing here reaches a network, and
a test that needed one would be a test that fails on a train.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from tests.unit.web.conftest import Walk
from tests.unit.web.page import read


def _git(root: Path, *arguments: str) -> str:
    done = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _as_git_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "reader@example.invalid")
    _git(root, "config", "user.name", "The Reader")
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts):
        _git(root, "add", "--", str(path.relative_to(root)))
    _git(root, "commit", "-q", "-m", "the project as it was")


@pytest.fixture
def remote(tmp_path_factory: pytest.TempPathFactory) -> Path:
    bare = tmp_path_factory.mktemp("remote") / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
    return bare


def _delivered(walk: Walk, walk_sql: Path, project_dir: Path):  # type: ignore[no-untyped-def]
    app, client, session = walk(walk_sql)
    assert client.post("/write").status_code == 200
    assert client.post("/deliver").status_code == 200
    return app, client, session


def test_a_push_sends_the_delivered_branch_to_the_remote(
    walk: Walk, walk_sql: Path, project_dir: Path, remote: Path
) -> None:
    _as_git_repo(project_dir)
    _git(project_dir, "remote", "add", "origin", str(remote))
    app, client, _session = _delivered(walk, walk_sql, project_dir)

    sent = client.post("/push")

    assert sent.status_code == 200
    branch = _git(project_dir, "branch", "--show-current")
    assert _git(project_dir, "rev-parse", f"refs/remotes/origin/{branch}") == _git(
        project_dir, "rev-parse", "HEAD"
    )
    assert app.config["dbtw_source"].pushed is not None


def test_pressing_push_twice_sends_once(
    walk: Walk, walk_sql: Path, project_dir: Path, remote: Path
) -> None:
    """A second press is a second thing this tool did to somebody's server.

    The screen already holds what the first push said, and git would answer
    the second with a different sentence -- so the button that has already
    gone stays reporting the trip it made.
    """
    _as_git_repo(project_dir)
    _git(project_dir, "remote", "add", "origin", str(remote))
    app, client, _session = _delivered(walk, walk_sql, project_dir)
    assert client.post("/push").status_code == 200
    first = app.config["dbtw_source"].pushed

    assert client.post("/push").status_code == 200

    assert app.config["dbtw_source"].pushed is first, "a second press pushed again"


def test_a_project_with_no_remote_is_refused_on_the_screen_that_delivered(
    walk: Walk, walk_sql: Path, project_dir: Path
) -> None:
    """The delivery still stands. What is missing is somewhere to send it,
    so the refusal is rendered with the branch and the files still on the
    page rather than on a screen of its own.
    """
    _as_git_repo(project_dir)
    app, client, _session = _delivered(walk, walk_sql, project_dir)

    refused = client.post("/push")

    assert refused.status_code == 409
    assert app.config["dbtw_source"].pushed is None
    page = read(refused.get_data(as_text=True))
    assert any("origin" in run for run in page.engine), "the refusal does not name the remote"
    assert [item for item in page.items if item == "written"], "the written files are gone"


def test_a_push_before_a_delivery_goes_back_rather_than_sending_anything(
    walk: Walk, walk_sql: Path, project_dir: Path, remote: Path
) -> None:
    """The button is not on a screen a reader reaches before delivering, so a
    press here is a form posted out of order rather than a state the walk
    offers -- and it must not push a branch that was never made.
    """
    _as_git_repo(project_dir)
    _git(project_dir, "remote", "add", "origin", str(remote))
    app, client, _session = walk(walk_sql)

    sent = client.post("/push")

    assert sent.status_code == 302
    assert app.config["dbtw_source"].pushed is None
    assert not _git(project_dir, "branch", "--list", "dbtw/*"), "a branch was made anyway"
