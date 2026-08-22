"""Tests for stacked branch pushes.

These run against a local bare repo standing in for GitHub (see the `origin` fixture),
so they exercise the real git commands without needing network access or SSH keys.
"""

import pytest

from dbt_training_wheels.services.github_service import GitHubError, StackEntry, push_stack_to_github
from tests.helpers import PROJECT_PATH, file_content_on, files_on, is_ancestor, model_file


def test_stack_creates_chained_branches(origin, config):
    """Each branch is based on the previous one, not on the default branch."""
    entries = [
        StackEntry(name="customer", files=[model_file("customer", "int__a")], commit_message="customer models"),
        StackEntry(name="insurance", files=[model_file("insurance", "int__b")], commit_message="insurance models"),
    ]

    result = push_stack_to_github(entries, "churn", config)

    assert result["success"] is True
    assert result["branches_pushed"] == 2
    assert [item["branch"] for item in result["stack"]] == [
        "dbt_training_wheels/churn--customer",
        "dbt_training_wheels/churn--insurance",
    ]
    # The second branch is based on the first, so the PRs can be reviewed in sequence
    assert result["stack"][0]["base"] == "main"
    assert result["stack"][1]["base"] == "dbt_training_wheels/churn--customer"
    assert result["stack"][1]["compare_url"] == (
        "https://github.com/acme/dbt/compare/dbt_training_wheels/churn--customer...dbt_training_wheels/churn--insurance?expand=1"
    )

    # Both branches reached the remote, and the chain is real
    assert is_ancestor(origin, result["stack"][0]["commit_sha"], result["stack"][1]["commit_sha"])


def test_each_branch_contains_only_its_own_files(origin, config):
    """The point of stacking: branch 1's diff doesn't include branch 2's models."""
    entries = [
        StackEntry(name="customer", files=[model_file("customer", "int__a")]),
        StackEntry(name="insurance", files=[model_file("insurance", "int__b")]),
    ]

    push_stack_to_github(entries, "churn", config)

    first = files_on(origin, "dbt_training_wheels/churn--customer")
    second = files_on(origin, "dbt_training_wheels/churn--insurance")

    assert f"{PROJECT_PATH}/models/customer/int__a.sql" in first
    assert f"{PROJECT_PATH}/models/insurance/int__b.sql" not in first

    # The second branch stacks on the first, so it carries both
    assert f"{PROJECT_PATH}/models/customer/int__a.sql" in second
    assert f"{PROJECT_PATH}/models/insurance/int__b.sql" in second


def test_domain_blocks_accumulate_down_the_stack(origin, config):
    """Each entry adds its own dbt_project.yml domain block on top of its parent's."""
    entries = [
        StackEntry(
            name="customer",
            files=[model_file("customer", "int__a")],
            domain_area="customer",
            active_layers=["intermediate"],
        ),
        StackEntry(
            name="insurance",
            files=[model_file("insurance", "int__b")],
            domain_area="insurance",
            active_layers=["intermediate", "mart"],
        ),
    ]

    push_stack_to_github(entries, "churn", config, dbt_project_name="my_dbt_project")

    path = f"{PROJECT_PATH}/dbt_project.yml"
    first = file_content_on(origin, "dbt_training_wheels/churn--customer", path)
    second = file_content_on(origin, "dbt_training_wheels/churn--insurance", path)

    assert "    customer:" in first
    assert "    insurance:" not in first

    assert "    customer:" in second
    assert "    insurance:" in second


def test_empty_entries_are_skipped(origin, config):
    """An entry with no files doesn't produce an empty branch."""
    entries = [
        StackEntry(name="customer", files=[model_file("customer", "int__a")]),
        StackEntry(name="empty", files=[]),
        StackEntry(name="insurance", files=[model_file("insurance", "int__b")]),
    ]

    result = push_stack_to_github(entries, "churn", config)

    assert [item["branch"] for item in result["stack"]] == [
        "dbt_training_wheels/churn--customer",
        "dbt_training_wheels/churn--insurance",
    ]
    # insurance stacks directly on customer, skipping the empty entry entirely
    assert result["stack"][1]["base"] == "dbt_training_wheels/churn--customer"


def test_unchanged_entry_does_not_create_a_branch(origin, config):
    """An entry whose files already match the repo is skipped, not committed empty."""
    from tests.helpers import DBT_PROJECT_YML

    existing = {
        "path": f"{PROJECT_PATH}/dbt_project.yml",
        "type": "config",
        "content": DBT_PROJECT_YML,
    }
    entries = [
        StackEntry(name="noop", files=[existing]),
        StackEntry(name="insurance", files=[model_file("insurance", "int__b")]),
    ]

    result = push_stack_to_github(entries, "churn", config)

    assert [item["branch"] for item in result["stack"]] == ["dbt_training_wheels/churn--insurance"]
    # With the no-op entry dropped, insurance falls back to branching off main
    assert result["stack"][0]["base"] == "main"


def test_branch_names_are_slugified(origin, config):
    """Domain names with spaces or punctuation still produce valid branch names."""
    entries = [StackEntry(name="Pet Insurance!", files=[model_file("insurance", "int__b")])]

    result = push_stack_to_github(entries, "Customer Churn", config)

    assert result["stack"][0]["branch"] == "dbt_training_wheels/customer-churn--pet-insurance"


def test_pr_linking_degrades_when_gh_is_unavailable(origin, config, monkeypatch):
    """Without gh credentials the branches still land; only the PRs are skipped."""
    from dbt_training_wheels.services import gh_stack_service
    from dbt_training_wheels.services.gh_stack_service import GhStackCapability

    monkeypatch.setattr(
        gh_stack_service,
        "check_gh_stack",
        lambda cwd=None: GhStackCapability(False, "The GitHub CLI is not authenticated"),
    )

    entries = [
        StackEntry(name="customer", files=[model_file("customer", "int__a")]),
        StackEntry(name="insurance", files=[model_file("insurance", "int__b")]),
    ]

    result = push_stack_to_github(entries, "churn", config, create_pr=True)

    assert result["success"] is True
    assert result["branches_pushed"] == 2
    assert result["pull_requests"] == []
    assert result["pr_linking"]["success"] is False
    assert "not authenticated" in result["pr_linking"]["reason"]
    # The compare URLs are still there as the fallback
    assert result["stack"][1]["compare_url"].endswith("?expand=1")


def test_pr_linking_runs_when_gh_is_available(origin, config, monkeypatch):
    from dbt_training_wheels.services import gh_stack_service
    from dbt_training_wheels.services.gh_stack_service import GhStackCapability

    linked = {}

    monkeypatch.setattr(
        gh_stack_service, "check_gh_stack", lambda cwd=None: GhStackCapability(True, version=(2, 95, 0))
    )

    def fake_link(branches, repo_dir, base_branch=None, open_prs=True):
        linked["branches"] = branches
        linked["base_branch"] = base_branch
        return {"success": True, "pull_requests": ["https://github.com/acme/dbt/pull/1"]}

    monkeypatch.setattr(gh_stack_service, "link_stack", fake_link)

    entries = [
        StackEntry(name="customer", files=[model_file("customer", "int__a")]),
        StackEntry(name="insurance", files=[model_file("insurance", "int__b")]),
    ]

    result = push_stack_to_github(entries, "churn", config, create_pr=True)

    # Branches are handed over bottom to top, based on the trunk we pushed onto
    assert linked["branches"] == ["dbt_training_wheels/churn--customer", "dbt_training_wheels/churn--insurance"]
    assert linked["base_branch"] == "main"
    assert result["pull_requests"] == ["https://github.com/acme/dbt/pull/1"]
    assert result["pr_linking"]["success"] is True


def test_no_pr_linking_unless_requested(origin, config, monkeypatch):
    from dbt_training_wheels.services import gh_stack_service

    def explode(cwd=None):
        raise AssertionError("gh should not be consulted when create_pr is False")

    monkeypatch.setattr(gh_stack_service, "check_gh_stack", explode)

    result = push_stack_to_github([StackEntry(name="a", files=[model_file("customer", "int__a")])], "churn", config)

    assert "pr_linking" not in result


def test_redeploying_the_same_conversion_is_refused(origin, config):
    """Branch names are deterministic, so a second deploy would collide."""
    from dbt_training_wheels.services.github_service import BranchesExistError

    entries = [
        StackEntry(name="customer", files=[model_file("customer", "int__a")]),
        StackEntry(name="insurance", files=[model_file("insurance", "int__b")]),
    ]
    push_stack_to_github(entries, "churn", config)

    regenerated = [
        StackEntry(name="customer", files=[model_file("customer", "int__a_v2")]),
        StackEntry(name="insurance", files=[model_file("insurance", "int__b")]),
    ]
    with pytest.raises(BranchesExistError) as exc:
        push_stack_to_github(regenerated, "churn", config)

    assert exc.value.branches == ["dbt_training_wheels/churn--customer", "dbt_training_wheels/churn--insurance"]


def test_force_replaces_the_previous_deploy(origin, config):
    """Regenerating after review comments updates the branches in place."""
    push_stack_to_github([StackEntry(name="customer", files=[model_file("customer", "int__old")])], "churn", config)
    assert f"{PROJECT_PATH}/models/customer/int__old.sql" in files_on(origin, "dbt_training_wheels/churn--customer")

    result = push_stack_to_github(
        [StackEntry(name="customer", files=[model_file("customer", "int__new")])],
        "churn",
        config,
        force=True,
    )

    assert result["success"] is True
    pushed = files_on(origin, "dbt_training_wheels/churn--customer")
    assert f"{PROJECT_PATH}/models/customer/int__new.sql" in pushed
    # The old model is gone - the branch was replaced, not appended to
    assert f"{PROJECT_PATH}/models/customer/int__old.sql" not in pushed


def test_force_aborts_if_the_branch_moved_underneath_us(origin, config):
    """--force-with-lease: someone else's push must not be silently discarded."""
    from tests.helpers import git

    push_stack_to_github([StackEntry(name="customer", files=[model_file("customer", "int__a")])], "churn", config)

    # Simulate a hand-made commit landing on the branch after we last looked
    work = origin.parent / "meddler"
    git(["clone", "-b", "dbt_training_wheels/churn--customer", str(origin), str(work)], cwd=origin.parent)
    git(["config", "user.email", "m@test"], cwd=work)
    git(["config", "user.name", "Meddler"], cwd=work)
    (work / "hand_edit.sql").write_text("select 1")
    git(["add", "-A"], cwd=work)
    git(["commit", "-m", "manual fix"], cwd=work)
    git(["push"], cwd=work)

    # A stale lease is only observable if we don't re-read the remote, so pin it here
    from dbt_training_wheels.services import github_service

    real = github_service._remote_branch_shas
    stale = {"dbt_training_wheels/churn--customer": "0" * 40}
    github_service._remote_branch_shas = lambda repo_dir, branches: stale
    try:
        with pytest.raises(github_service.GitHubError):
            push_stack_to_github(
                [StackEntry(name="customer", files=[model_file("customer", "int__b")])],
                "churn",
                config,
                force=True,
            )
    finally:
        github_service._remote_branch_shas = real

    # The manual commit survived
    assert "hand_edit.sql" in files_on(origin, "dbt_training_wheels/churn--customer")


def test_stack_can_land_on_another_branch(origin, config):
    """A stack doesn't have to target the repo default - e.g. an existing feature branch."""
    from tests.helpers import git

    # Create a feature branch on the remote to stack onto
    work = origin.parent / "feature"
    git(["clone", str(origin), str(work)], cwd=origin.parent)
    git(["config", "user.email", "f@test"], cwd=work)
    git(["config", "user.name", "Feature"], cwd=work)
    git(["checkout", "-b", "release/q4"], cwd=work)
    (work / "release_note.md").write_text("q4")
    git(["add", "-A"], cwd=work)
    git(["commit", "-m", "start release"], cwd=work)
    git(["push", "-u", "origin", "release/q4"], cwd=work)

    entries = [
        StackEntry(name="customer", files=[model_file("customer", "int__a")]),
        StackEntry(name="insurance", files=[model_file("insurance", "int__b")]),
    ]

    result = push_stack_to_github(entries, "churn", config, base_branch="release/q4")

    assert result["base_branch"] == "release/q4"
    assert result["stack"][0]["base"] == "release/q4"
    assert result["stack"][1]["base"] == "dbt_training_wheels/churn--customer"
    assert result["stack"][0]["compare_url"].startswith(
        "https://github.com/acme/dbt/compare/release/q4...dbt_training_wheels/churn--customer"
    )
    # The branch really descends from the feature branch, not from main
    assert "release_note.md" in files_on(origin, "dbt_training_wheels/churn--customer")


def test_an_unknown_base_branch_is_a_clear_error(origin, config):
    entries = [StackEntry(name="customer", files=[model_file("customer", "int__a")])]

    with pytest.raises(GitHubError, match="doesn't exist"):
        push_stack_to_github(entries, "churn", config, base_branch="no/such/branch")


def test_empty_base_branch_uses_the_configured_default(origin, config):
    entries = [StackEntry(name="customer", files=[model_file("customer", "int__a")])]

    result = push_stack_to_github(entries, "churn", config, base_branch="")

    assert result["base_branch"] == "main"


def test_raises_when_every_entry_is_empty(origin, config):
    entries = [StackEntry(name="a", files=[]), StackEntry(name="b", files=[])]

    with pytest.raises(GitHubError, match="No files to push"):
        push_stack_to_github(entries, "churn", config)


def test_raises_without_a_repository(config):
    config.repository = ""

    with pytest.raises(GitHubError, match="repository not configured"):
        push_stack_to_github([StackEntry(name="a", files=[model_file("customer", "int__a")])], "churn", config)
