"""Tests for the gh stack integration.

`gh stack link` is stubbed at the subprocess boundary so these assert the capability
gate and the exact command we build, without needing a real GitHub repository.
"""

import subprocess

import pytest

from dbt_training_wheels.services import gh_stack_service
from dbt_training_wheels.services.gh_stack_service import check_gh_stack, link_stack

GOOD_VERSION = "gh version 2.95.0 (2025-01-01)\nhttps://github.com/cli/cli/releases/tag/v2.95.0"
OLD_VERSION = "gh version 2.78.0 (2025-08-21)\nhttps://github.com/cli/cli/releases/tag/v2.78.0"
STACK_HELP = "Stacked PRs let you break a large change into a chain of pull requests"
AUTH_OK = "github.com\n  ✓ Logged in to github.com account someone"


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    """Stand-in for gh_stack_service._run that records commands and replays responses."""

    def __init__(self, responses=None, default=None):
        self.calls = []
        self.responses = responses or {}
        self.default = default or completed(0)

    def __call__(self, args, cwd=None, timeout=60):
        self.calls.append(args)
        joined = " ".join(args)
        for fragment, response in self.responses.items():
            if fragment in joined:
                return response
        return self.default

    @property
    def commands(self):
        return [" ".join(call) for call in self.calls]


def healthy_responses(**overrides):
    responses = {
        "gh --version": completed(0, GOOD_VERSION),
        "gh stack --help": completed(0, STACK_HELP),
        "gh auth status": completed(0, AUTH_OK),
    }
    responses.update(overrides)
    return responses


@pytest.fixture
def fake_run(monkeypatch):
    def install(responses=None, default=None):
        runner = FakeRun(responses, default)
        monkeypatch.setattr(gh_stack_service, "_run", runner)
        return runner

    return install


# ---------------------------------------------------------------- capability


def test_available_when_everything_is_in_place(fake_run):
    fake_run(healthy_responses())

    capability = check_gh_stack()

    assert capability.available
    assert capability.version == (2, 95, 0)


def test_old_gh_is_rejected(fake_run):
    """The host CLI is 2.78; gh stack needs 2.90+."""
    fake_run(healthy_responses(**{"gh --version": completed(0, OLD_VERSION)}))

    capability = check_gh_stack()

    assert not capability.available
    assert "2.90" in capability.reason
    assert capability.version_string == "2.78.0"


def test_missing_extension_is_reported(fake_run):
    fake_run(healthy_responses(**{"gh stack --help": completed(1, "", "unknown command")}))

    capability = check_gh_stack()

    assert not capability.available
    assert "gh-stack" in capability.reason


def test_missing_auth_is_reported_before_a_missing_extension(fake_run):
    """`gh extension list` comes back empty without credentials, so auth is the real cause."""
    fake_run(
        healthy_responses(
            **{
                "gh auth status": completed(1, "", "not logged in"),
                "gh stack --help": completed(1, "", "unknown command"),
            }
        )
    )

    capability = check_gh_stack()

    assert "GH_TOKEN" in capability.reason
    assert "gh-stack" not in capability.reason


def test_unauthenticated_gh_is_reported(fake_run):
    """The common case: SSH keys work for git, but gh has no credentials."""
    fake_run(healthy_responses(**{"gh auth status": completed(1, "", "not logged in")}))

    capability = check_gh_stack()

    assert not capability.available
    assert "GH_TOKEN" in capability.reason


def test_missing_gh_binary_is_reported(monkeypatch):
    def explode(args, cwd=None, timeout=60):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(gh_stack_service, "_run", explode)

    capability = check_gh_stack()

    assert not capability.available
    assert "not installed" in capability.reason


# ---------------------------------------------------------------- linking


def test_link_builds_the_expected_command(fake_run):
    runner = fake_run(default=completed(0, "https://github.com/acme/dbt/pull/1"))

    link_stack(["dbt_training_wheels/churn--customer", "dbt_training_wheels/churn--insurance"], "/tmp/repo", base_branch="main")

    assert runner.commands == ["gh stack link --base main --open dbt_training_wheels/churn--customer dbt_training_wheels/churn--insurance"]


def test_branch_order_is_preserved(fake_run):
    """gh stack link takes branches bottom to top, which is our merge order."""
    runner = fake_run()

    link_stack(["one", "two", "three"], "/tmp/repo")

    assert runner.calls[0][-3:] == ["one", "two", "three"]


def test_draft_prs_when_open_is_false(fake_run):
    runner = fake_run()

    link_stack(["one"], "/tmp/repo", open_prs=False)

    assert "--open" not in runner.commands[0]


def test_pull_request_urls_are_parsed(fake_run):
    output = (
        "Created https://github.com/acme/dbt/pull/41\n"
        "Created https://github.com/acme/dbt/pull/42\n"
        "Stacked 2 pull requests\n"
    )
    fake_run(default=completed(0, output))

    result = link_stack(["one", "two"], "/tmp/repo")

    assert result["success"]
    assert result["pull_requests"] == [
        "https://github.com/acme/dbt/pull/41",
        "https://github.com/acme/dbt/pull/42",
    ]


def test_failure_is_reported_not_raised(fake_run):
    """The branches are already pushed, so a link failure must not blow up the deploy."""
    fake_run(default=completed(1, "", "could not resolve to a Repository"))

    result = link_stack(["one"], "/tmp/repo")

    assert result["success"] is False
    assert "Repository" in result["reason"]
    assert result["pull_requests"] == []


def test_no_branches_is_a_no_op(fake_run):
    runner = fake_run()

    result = link_stack([], "/tmp/repo")

    assert result["success"] is False
    assert runner.calls == []
