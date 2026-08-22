"""Tests for downloading an uploaded folder grouped by what feeds off what.

This is the path for someone who isn't converting to dbt at all: upload a folder of
subfolders, get the same SQL back sorted into groups that depend on each other, in run
order. Nothing is converted, nothing is pushed.
"""

import pytest
from flask import Flask

from dbt_training_wheels.routes.api import analysis as analysis_route
from dbt_training_wheels.services import query_service
from dbt_training_wheels.services.query_service import grouped_source_files, load_conversions

BASE = "CREATE OR REPLACE TABLE `proj.sandbox.cust_base` AS SELECT 1 AS id;"
FEATURES = (
    "CREATE OR REPLACE TABLE `proj.sandbox.cust_feat` AS\n"
    "SELECT b.id FROM `proj.sandbox.cust_base` b JOIN `proj.dwh.seed` s USING (id);"
)
CLAIMS = "CREATE OR REPLACE TABLE `proj.sandbox.claims` AS SELECT 1 AS id;"


@pytest.fixture
def uploaded(tmp_path, monkeypatch):
    """base and features feed each other; claims shares nothing with either."""
    root = tmp_path / "source_sql_file"
    (root / "churn").mkdir(parents=True)
    (root / "churn" / "base.sql").write_text(BASE)
    (root / "churn" / "claims.sql").write_text(CLAIMS)
    (root / "churn" / "features.sql").write_text(FEATURES)
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(root))
    return root


@pytest.fixture
def lone(tmp_path, monkeypatch):
    root = tmp_path / "source_sql_file"
    root.mkdir(parents=True)
    (root / "solo.sql").write_text(BASE)
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(root))
    return root


def _paths(files):
    return [f["path"] for f in files]


def _by_path(files, path):
    return next(f["content"] for f in files if f["path"] == path)


# ---------------------------------------------------------------- layout


def test_each_group_gets_its_own_folder(uploaded):
    files = grouped_source_files(load_conversions()[0])

    assert _paths(files) == [
        "GROUPS.md",
        "group-1/01_base.sql",
        "group-1/02_features.sql",
        "group-2/claims.sql",
    ]


def test_files_in_a_group_are_numbered_in_run_order(uploaded):
    """The order is the point - features reads the table base creates."""
    files = grouped_source_files(load_conversions()[0])

    ordered = [p for p in _paths(files) if p.startswith("group-1/")]

    assert ordered == ["group-1/01_base.sql", "group-1/02_features.sql"]


def test_a_group_of_one_is_not_numbered(uploaded):
    """Nothing to order against, so a leading 01_ would just be noise."""
    files = grouped_source_files(load_conversions()[0])

    assert "group-2/claims.sql" in _paths(files)


def test_a_single_group_gets_no_group_folder(tmp_path, monkeypatch):
    """One unit of work shouldn't sit inside a folder called group-1."""
    root = tmp_path / "source_sql_file"
    (root / "churn").mkdir(parents=True)
    (root / "churn" / "base.sql").write_text(BASE)
    (root / "churn" / "features.sql").write_text(FEATURES)
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(root))

    files = grouped_source_files(load_conversions()[0])

    assert _paths(files) == ["GROUPS.md", "01_base.sql", "02_features.sql"]


def test_a_lone_upload_is_just_the_file(lone):
    files = grouped_source_files(load_conversions()[0])

    assert _paths(files) == ["GROUPS.md", "solo.sql"]


def test_an_empty_conversion_produces_nothing():
    assert grouped_source_files({"name": "empty", "queries": []}) == []


# ---------------------------------------------------------------- content


def test_the_sql_is_returned_exactly_as_uploaded(uploaded):
    """Nothing is converted - no refs, no sources, no layer prefixes."""
    files = grouped_source_files(load_conversions()[0])

    content = _by_path(files, "group-1/01_base.sql")

    assert content.strip() == BASE.strip()
    assert "{{" not in content


def test_nothing_dbt_shaped_is_added(uploaded):
    files = grouped_source_files(load_conversions()[0])

    assert not any(p.endswith((".yml", ".yaml")) for p in _paths(files))
    assert not any("models/" in p for p in _paths(files))


def test_the_readme_explains_the_split(uploaded):
    files = grouped_source_files(load_conversions()[0])

    readme = _by_path(files, "GROUPS.md")

    assert "2 independent groups" in readme
    assert "## group-1" in readme and "## group-2" in readme
    assert "`features` reads a table `base` creates." in readme
    assert "shares no tables" in readme


def test_the_readme_says_so_when_there_is_one_query(lone):
    readme = _by_path(grouped_source_files(load_conversions()[0]), "GROUPS.md")

    assert "nothing to group" in readme.lower()


# ---------------------------------------------------------------- endpoint


@pytest.fixture
def client(uploaded, monkeypatch):
    monkeypatch.setattr(analysis_route, "get_org_config", lambda: None)
    app = Flask(__name__)
    app.register_blueprint(analysis_route.analysis_bp, url_prefix="/api")
    app.config["TESTING"] = True
    return app.test_client()


def test_the_endpoint_returns_the_grouped_files(client):
    response = client.get("/api/grouped-source/1")

    assert response.status_code == 200
    assert _paths(response.get_json()) == [
        "GROUPS.md",
        "group-1/01_base.sql",
        "group-1/02_features.sql",
        "group-2/claims.sql",
    ]


def test_any_query_in_the_upload_returns_the_whole_folder(client):
    """You don't have to pick the right subfolder - the upload is the unit."""
    from_claims = client.get("/api/grouped-source/2").get_json()
    from_base = client.get("/api/grouped-source/1").get_json()

    assert _paths(from_claims) == _paths(from_base)


def test_an_unknown_query_is_reported(client):
    assert client.get("/api/grouped-source/999").status_code >= 400
