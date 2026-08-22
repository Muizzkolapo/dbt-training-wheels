"""Route-level tests for deploying a conversion as independent groups.

One uploaded folder is still one deploy, but not necessarily one stack. Subfolders that
feed off each other go out as a stack in merge order; subfolders that share nothing go
out separately, off the base branch, so unrelated work doesn't queue behind itself.

These run against the bare-repo `origin` fixture, so the branches and their bases are
read back from real git rather than asserted against a mock.
"""

import pytest
from flask import Flask

from dbt_training_wheels.config_schema import load_config_from_dict
from dbt_training_wheels.routes.api import github as github_route
from dbt_training_wheels.services import file_generator, query_service
from tests.helpers import PROJECT_PATH

# Two chains that share nothing, plus a subfolder related to neither.
BASE = "CREATE OR REPLACE TABLE `proj.sandbox.cust_base` AS SELECT 1 AS id;"
FEATURES = (
    "CREATE OR REPLACE TABLE `proj.sandbox.cust_feat` AS\n"
    "SELECT b.id FROM `proj.sandbox.cust_base` b JOIN `proj.dwh.seed` s USING (id);"
)
CLAIMS = "CREATE OR REPLACE TABLE `proj.sandbox.claims` AS SELECT 1 AS id;"
LOOKUP = "CREATE OR REPLACE TABLE `proj.sandbox.lookup` AS SELECT 1 AS id;"


def _config():
    return load_config_from_dict(
        {
            "org_name": "test",
            "dbt_project_name": "my_dbt_project",
            "github": {
                "enabled": True,
                "repository": "acme/dbt",
                "default_branch": "main",
                "branch_prefix": "dbt_training_wheels/",
            },
            "projects": {"p": {"dbt_config": {"github": {"base_path": PROJECT_PATH}}}},
        }
    )


@pytest.fixture
def client(tmp_path, monkeypatch, origin):
    """An upload of four subfolders forming three independent groups.

    Sorted filenames decide query ids: base=1, claims=2, features=3, lookup=4.
    """
    sql_dir = tmp_path / "source_sql_file"
    (sql_dir / "churn").mkdir(parents=True)
    (sql_dir / "churn" / "base.sql").write_text(BASE)
    (sql_dir / "churn" / "claims.sql").write_text(CLAIMS)
    (sql_dir / "churn" / "features.sql").write_text(FEATURES)
    (sql_dir / "churn" / "lookup.sql").write_text(LOOKUP)
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(sql_dir))
    monkeypatch.setattr(file_generator, "_scan_sources_via_git_clone", lambda *a, **k: set())
    monkeypatch.setattr(github_route, "get_org_config", lambda: _config())

    app = Flask(__name__)
    app.register_blueprint(github_route.github_bp, url_prefix="/api")
    app.config["TESTING"] = True
    return app.test_client()


def _push(client, query_id=1, **body):
    payload = {"branch_name": "dbt_training_wheels/churn", "project": "p", **body}
    return client.post(f"/api/push-to-github/{query_id}", json=payload)


def _groups(body):
    return [g["domains"] for g in body["groups"]]


# ---------------------------------------------------------------- the split


def test_independent_subfolders_deploy_as_separate_groups(client):
    response = _push(client)

    assert response.status_code == 200
    body = response.get_json()
    assert body["is_grouped"] is True
    assert _groups(body) == [["base", "features"], ["claims"], ["lookup"]]


def test_a_group_that_feeds_itself_is_a_stack(client):
    body = _push(client).get_json()

    related = body["groups"][0]
    assert related["is_stack"] is True
    assert [entry["name"] for entry in related["stack"]] == ["base", "features"]
    # Producer merges first, consumer stacks on top of it
    assert related["stack"][0]["base"] == "main"
    assert related["stack"][1]["base"] == "dbt_training_wheels/churn--base"


def test_an_unrelated_group_is_a_plain_pull_request(client):
    body = _push(client).get_json()

    lone = body["groups"][2]
    assert lone["is_stack"] is False
    assert lone["domains"] == ["lookup"]


def test_groups_do_not_chain_onto_each_other(client):
    """The whole point: unrelated work starts from the trunk, not from group one."""
    body = _push(client).get_json()

    claims = body["groups"][1]
    assert claims["branch"] == "dbt_training_wheels/churn--claims"
    # Not based on anything from the first group
    assert "base" not in claims["branch"]


def test_each_group_gets_its_own_branch(client):
    body = _push(client).get_json()

    branches = [g["branch"] for g in body["groups"]]
    assert len(set(branches)) == len(branches), branches


def test_deploying_from_any_subfolder_gives_the_same_split(client):
    """Opening 'lookup' and deploying still pushes the whole conversion, same groups."""
    from_base = _push(client, query_id=1).get_json()
    assert _groups(from_base) == [["base", "features"], ["claims"], ["lookup"]]


def test_the_total_file_count_covers_every_group(client):
    body = _push(client).get_json()

    assert body["files_pushed"] == sum(g["files_pushed"] for g in body["groups"])


def test_a_conversion_with_one_group_keeps_the_old_response_shape(tmp_path, monkeypatch, origin):
    """The common case is unchanged - no 'groups' key, no new shape to handle."""
    sql_dir = tmp_path / "source_sql_file"
    (sql_dir / "churn").mkdir(parents=True)
    (sql_dir / "churn" / "base.sql").write_text(BASE)
    (sql_dir / "churn" / "features.sql").write_text(FEATURES)
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(sql_dir))
    monkeypatch.setattr(file_generator, "_scan_sources_via_git_clone", lambda *a, **k: set())
    monkeypatch.setattr(github_route, "get_org_config", lambda: _config())

    app = Flask(__name__)
    app.register_blueprint(github_route.github_bp, url_prefix="/api")
    app.config["TESTING"] = True

    body = (
        app.test_client().post("/api/push-to-github/1", json={"branch_name": "dbt_training_wheels/churn", "project": "p"}).get_json()
    )

    assert "is_grouped" not in body
    assert body["is_stack"] is True
    assert [entry["name"] for entry in body["stack"]] == ["base", "features"]
