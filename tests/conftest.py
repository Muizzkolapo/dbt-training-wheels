"""Shared pytest configuration and fixtures.

Puts the project root on sys.path so tests can import dbt_training_wheels without an editable
install, matching how tests/run_analysis_tests.py does it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from dbt_training_wheels.config_schema import GitHubConfig  # noqa: E402
from dbt_training_wheels.services import github_service  # noqa: E402
from tests.helpers import DBT_PROJECT_YML, PROJECT_PATH, git  # noqa: E402


@pytest.fixture
def origin(tmp_path, monkeypatch):
    """A bare repo standing in for GitHub, seeded with one commit on main.

    Also redirects clones at the local repo, so tests exercise the real git commands
    without network access or SSH keys.
    """
    bare = tmp_path / "origin.git"
    git(["init", "--bare", str(bare)], cwd=tmp_path)

    seed = tmp_path / "seed"
    seed.mkdir()
    git(["init"], cwd=seed)
    git(["symbolic-ref", "HEAD", "refs/heads/main"], cwd=seed)
    git(["config", "user.email", "seed@test.local"], cwd=seed)
    git(["config", "user.name", "Seed"], cwd=seed)

    project_dir = seed / PROJECT_PATH
    project_dir.mkdir(parents=True)
    (project_dir / "dbt_project.yml").write_text(DBT_PROJECT_YML)
    git(["add", "-A"], cwd=seed)
    git(["commit", "-m", "seed"], cwd=seed)
    git(["remote", "add", "origin", str(bare)], cwd=seed)
    git(["push", "origin", "main"], cwd=seed)

    monkeypatch.setattr(github_service, "_ssh_url", lambda repository: f"file://{bare}")
    return bare


@pytest.fixture
def config():
    """GitHubConfig pointing at the fake origin."""
    return GitHubConfig(
        enabled=True,
        repository="acme/dbt",
        default_branch="main",
        branch_prefix="dbt_training_wheels/",
    )
