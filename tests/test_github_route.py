"""Route-level tests for the deploy endpoint's single-vs-stack decision.

The pieces underneath are unit tested; this covers the branching in
routes/api/github.py itself - which of the three push paths a request takes, and how
the recoverable cases surface to the UI.
"""

import pytest
from flask import Flask

from dbt_training_wheels.config_schema import load_config_from_dict
from dbt_training_wheels.routes.api import github as github_route
from dbt_training_wheels.services import file_generator, query_service
from tests.helpers import PROJECT_PATH

# Two subfolder queries, the second reading the first's table
SAMPLE1 = "CREATE OR REPLACE TABLE `proj.sandbox.customers_90d` AS SELECT 1 AS customer_id;"
SAMPLE2 = (
    "CREATE OR REPLACE TABLE `proj.ecomm.sku` AS\n"
    "SELECT a.article_id FROM `proj.dwh.items` a\n"
    "JOIN `proj.sandbox.customers_90d` c USING (customer_id);"
)

# One query writing to two datasets, so configured domains can split it
MIXED = (
    "CREATE OR REPLACE TABLE `proj.customer_mart.cust_base` AS SELECT id FROM `proj.raw.customers`;\n"
    "CREATE OR REPLACE TABLE `proj.insurance_mart.claims` AS SELECT 1 AS id;"
)

DOMAINS = {
    "customer": {"datasets": ["customer_mart"]},
    "insurance": {"datasets": ["insurance_mart"]},
}


def _config(domains=None):
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
            "projects": {
                "p": {
                    "dbt_config": {
                        "github": {"base_path": PROJECT_PATH},
                        **({"domains": domains} if domains else {}),
                    }
                }
            },
        }
    )


@pytest.fixture
def client(tmp_path, monkeypatch, origin):
    """Deploy endpoint wired to a temp query directory and the fake remote."""
    sql_dir = tmp_path / "source_sql_file"
    (sql_dir / "demo").mkdir(parents=True)
    (sql_dir / "demo" / "sample1.sql").write_text(SAMPLE1)
    (sql_dir / "demo" / "sample2.sql").write_text(SAMPLE2)
    (sql_dir / "mixed.sql").write_text(MIXED)
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(sql_dir))

    # Never reach for the real GitHub when scanning for existing sources
    monkeypatch.setattr(file_generator, "_scan_sources_via_git_clone", lambda *a, **k: set())

    app = Flask(__name__)
    app.register_blueprint(github_route.github_bp, url_prefix="/api")
    app.config["TESTING"] = True
    return app.test_client()


def _use_config(monkeypatch, config):
    monkeypatch.setattr(github_route, "get_org_config", lambda: config)


def _query_id(filename):
    """Queries are numbered by sorted filename: demo/sample1, demo/sample2, mixed."""
    return {"demo/sample1.sql": 1, "demo/sample2.sql": 2, "mixed.sql": 3}[filename]


def _push(client, query_id, **body):
    payload = {"branch_name": "dbt_training_wheels/churn", "project": "p", **body}
    return client.post(f"/api/push-to-github/{query_id}", json=payload)


# ---------------------------------------------------------------- the decision


def test_a_single_domain_conversion_is_one_pull_request(client, monkeypatch):
    """No stack language for a chain of one - it keeps the plain branch name."""
    _use_config(monkeypatch, _config())

    response = _push(client, _query_id("mixed.sql"))

    assert response.status_code == 200
    body = response.get_json()
    assert body["is_stack"] is False
    assert body["branch"] == "dbt_training_wheels/churn"


def test_deploy_always_covers_the_whole_conversion(client, monkeypatch):
    """One uploaded folder is one deploy - no flag needed, and none accepted."""
    _use_config(monkeypatch, _config())

    response = _push(client, _query_id("demo/sample1.sql"))

    assert response.status_code == 200
    body = response.get_json()
    assert body["is_stack"] is True
    assert [entry["name"] for entry in body["stack"]] == ["sample1", "sample2"]


def test_deploying_from_any_domain_gives_the_same_stack(client, monkeypatch):
    """Opening the second domain and deploying pushes the same conversion."""
    _use_config(monkeypatch, _config())

    response = _push(client, _query_id("demo/sample2.sql"))

    assert response.status_code == 200
    body = response.get_json()
    assert body["is_stack"] is True
    # Ordered by the dependency: sample2 reads sample1's table
    assert [entry["name"] for entry in body["stack"]] == ["sample1", "sample2"]
    assert body["stack"][0]["base"] == "main"
    assert body["stack"][1]["base"] == "dbt_training_wheels/churn--sample1"


def test_a_cross_domain_query_stacks_without_siblings(client, monkeypatch):
    """The other route into a stack: one query whose models span configured domains."""
    _use_config(monkeypatch, _config(DOMAINS))

    response = _push(client, _query_id("mixed.sql"))

    assert response.status_code == 200
    body = response.get_json()
    assert body["is_stack"] is True
    assert sorted(entry["name"] for entry in body["stack"]) == ["customer", "insurance"]


# ---------------------------------------------------------------- recoverable cases


def test_redeploying_surfaces_a_recoverable_conflict(client, monkeypatch):
    _use_config(monkeypatch, _config())
    assert _push(client, _query_id("demo/sample1.sql")).status_code == 200

    response = _push(client, _query_id("demo/sample1.sql"))

    assert response.status_code == 400
    details = response.get_json()["error"]["details"]
    assert details["can_overwrite"] is True
    assert details["conflicts"] == ["dbt_training_wheels/churn--sample1", "dbt_training_wheels/churn--sample2"]


def test_force_push_replaces_the_previous_deploy(client, monkeypatch):
    _use_config(monkeypatch, _config())
    assert _push(client, _query_id("demo/sample1.sql")).status_code == 200

    response = _push(client, _query_id("demo/sample1.sql"), force_push=True)

    assert response.status_code == 200
    assert response.get_json()["is_stack"] is True


def test_an_unknown_base_branch_is_rejected(client, monkeypatch):
    _use_config(monkeypatch, _config())

    response = _push(client, _query_id("demo/sample1.sql"), base_branch="no/such/branch")

    assert response.status_code >= 400
    assert "doesn't exist" in response.get_json()["error"]["user_message"]


# ---------------------------------------------------------------- guards


def test_github_must_be_configured(client, monkeypatch):
    config = _config()
    config.github.enabled = False
    _use_config(monkeypatch, config)

    response = _push(client, _query_id("mixed.sql"))

    assert response.status_code == 400
    assert "not configured" in response.get_json()["error"]["user_message"]


def test_a_branch_name_is_required(client, monkeypatch):
    _use_config(monkeypatch, _config())

    response = client.post(f"/api/push-to-github/{_query_id('mixed.sql')}", json={"project": "p"})

    assert response.status_code == 400
    assert "branch name" in response.get_json()["error"]["user_message"]


def test_status_reports_whether_pull_requests_can_be_opened(client, monkeypatch):
    """So the deploy page can say so before pushing, not after."""
    from dbt_training_wheels.services import gh_stack_service
    from dbt_training_wheels.services.gh_stack_service import GhStackCapability

    _use_config(monkeypatch, _config())
    monkeypatch.setattr(gh_stack_service, "check_gh_stack", lambda: GhStackCapability(True, version=(2, 95, 0)))

    body = client.get("/api/github/status").get_json()

    assert body["pull_requests"] == {"available": True, "reason": "", "gh_version": "2.95.0"}


def test_status_explains_why_pull_requests_are_unavailable(client, monkeypatch):
    from dbt_training_wheels.services import gh_stack_service
    from dbt_training_wheels.services.gh_stack_service import GhStackCapability

    _use_config(monkeypatch, _config())
    monkeypatch.setattr(
        gh_stack_service,
        "check_gh_stack",
        lambda: GhStackCapability(False, "The gh-stack extension is not installed", (2, 95, 0)),
    )

    body = client.get("/api/github/status").get_json()

    assert body["pull_requests"]["available"] is False
    assert "gh-stack" in body["pull_requests"]["reason"]


def test_a_missing_query_is_a_404(client, monkeypatch):
    _use_config(monkeypatch, _config())

    response = _push(client, 999)

    assert response.status_code == 404
