"""Regression tests for the single-branch push path.

push_to_github backs the deploy route. It shares its internals with
push_stack_to_github, so these guard the shared helpers against changes made for
stacking.
"""

import pytest

from dbt_training_wheels.services.github_service import GitHubError, push_to_github
from tests.helpers import PROJECT_PATH, branch_exists, file_content_on, files_on, model_file


def test_pushes_files_to_a_new_branch(origin, config):
    files = [model_file("customer", "int__a"), model_file("customer", "mart__a")]

    result = push_to_github(files, "dbt_training_wheels/churn", "Add dbt models", config)

    assert result["success"] is True
    assert result["branch"] == "dbt_training_wheels/churn"
    assert result["files_pushed"] == 2
    assert result["commit_sha"] != "unknown"
    assert branch_exists(origin, "dbt_training_wheels/churn")

    pushed = files_on(origin, "dbt_training_wheels/churn")
    assert f"{PROJECT_PATH}/models/customer/int__a.sql" in pushed
    assert f"{PROJECT_PATH}/models/customer/mart__a.sql" in pushed


def test_branches_off_the_default_branch(origin, config):
    push_to_github([model_file("customer", "int__a")], "dbt_training_wheels/churn", "Add dbt models", config)

    # The seed file from main is still present, so the branch descends from it
    assert f"{PROJECT_PATH}/dbt_project.yml" in files_on(origin, "dbt_training_wheels/churn")


def test_adds_the_domain_block_to_dbt_project_yml(origin, config):
    push_to_github(
        [model_file("customer", "int__a")],
        "dbt_training_wheels/churn",
        "Add dbt models",
        config,
        domain_area="customer",
        dbt_project_name="my_dbt_project",
        active_layers=["intermediate", "mart"],
    )

    content = file_content_on(origin, "dbt_training_wheels/churn", f"{PROJECT_PATH}/dbt_project.yml")

    assert "    customer:" in content
    assert '+tags: "dbt_training_wheels_customer"' in content
    assert '+tags: "dbt_int_customer"' in content
    assert '+tags: "dbt_mart_customer"' in content
    # Layers with no models don't get a block
    assert "dbt_stg_customer" not in content


def test_skips_the_domain_block_when_it_already_exists(origin, config):
    push_to_github(
        [model_file("customer", "int__a")],
        "dbt_training_wheels/churn",
        "Add dbt models",
        config,
        domain_area="customer",
        dbt_project_name="my_dbt_project",
        active_layers=["intermediate"],
    )
    content = file_content_on(origin, "dbt_training_wheels/churn", f"{PROJECT_PATH}/dbt_project.yml")

    assert content.count("    customer:") == 1


def test_models_path_prefix_is_applied(origin, config):
    files = [{"path": "models/customer/int__a.sql", "type": "model", "content": "select 1\n"}]

    push_to_github(files, "dbt_training_wheels/churn", "Add dbt models", config, models_path=PROJECT_PATH)

    assert f"{PROJECT_PATH}/models/customer/int__a.sql" in files_on(origin, "dbt_training_wheels/churn")


def test_raises_without_a_repository(config):
    config.repository = ""

    with pytest.raises(GitHubError, match="repository not configured"):
        push_to_github([model_file("customer", "int__a")], "dbt_training_wheels/churn", "Add dbt models", config)
