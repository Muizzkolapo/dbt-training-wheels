"""Tests for folder uploads.

Design decision: one query per leaf subfolder. Uploading a folder produces one merged
SQL file per subfolder ('churn/customer/*.sql' -> 'churn/customer.sql'), each of which
becomes its own query, instead of flattening everything into a single script.
"""

from io import BytesIO

import pytest
from flask import Flask

from dbt_training_wheels.routes.api import upload as upload_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A Flask test client with uploads redirected to a temp directory."""
    monkeypatch.setattr(upload_module, "SQL_DIRECTORY", str(tmp_path / "source_sql_file"))

    app = Flask(__name__)
    app.register_blueprint(upload_module.upload_bp, url_prefix="/api")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def sql_dir(tmp_path):
    return tmp_path / "source_sql_file"


def upload(client, entries, overwrite=False):
    """POST files to /api/upload-folder.

    Args:
        entries: list of (relative_path, sql_content) tuples
    """
    data = {
        "files": [(BytesIO(content.encode()), path.rsplit("/", 1)[-1]) for path, content in entries],
        "paths": [path for path, _ in entries],
    }
    url = "/api/upload-folder?overwrite=true" if overwrite else "/api/upload-folder"
    return client.post(url, data=data, content_type="multipart/form-data")


def test_each_subfolder_becomes_its_own_merged_file(client, sql_dir):
    response = upload(
        client,
        [
            ("churn/customer/base.sql", "SELECT 1"),
            ("churn/customer/features.sql", "SELECT 2"),
            ("churn/insurance/claims.sql", "SELECT 3"),
        ],
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["queries_created"] == 2
    assert body["merged_files"] == ["churn/customer.sql", "churn/insurance.sql"]
    assert body["source_files_count"] == 3

    assert (sql_dir / "churn" / "customer.sql").exists()
    assert (sql_dir / "churn" / "insurance.sql").exists()
    # Each merged file holds only its own subfolder's SQL
    customer = (sql_dir / "churn" / "customer.sql").read_text()
    assert "SELECT 1" in customer and "SELECT 2" in customer and "SELECT 3" not in customer
    assert "SELECT 3" in (sql_dir / "churn" / "insurance.sql").read_text()


def test_flat_folder_behaves_like_before(client, sql_dir):
    """A folder with no subfolders still merges into a single <folder>.sql."""
    response = upload(
        client,
        [
            ("churn/a.sql", "SELECT 1"),
            ("churn/b.sql", "SELECT 2"),
        ],
    )

    body = response.get_json()
    assert body["merged_files"] == ["churn.sql"]
    merged = (sql_dir / "churn.sql").read_text()
    assert merged == "SELECT 1;\n\nSELECT 2;"


def test_merge_order_is_path_order_not_arrival_order(client, sql_dir):
    """Numeric prefixes control statement order even if the browser sends files shuffled."""
    response = upload(
        client,
        [
            ("mig/02_features.sql", "SELECT 'second'"),
            ("mig/00_base.sql", "SELECT 'first'"),
            ("mig/01_prep.sql", "SELECT 'middle'"),
        ],
    )

    assert response.status_code == 200
    merged = (sql_dir / "mig.sql").read_text()
    assert merged.index("first") < merged.index("middle") < merged.index("second")


def test_statements_get_semicolons_only_when_missing(client, sql_dir):
    upload(
        client,
        [
            ("m/a.sql", "SELECT 1;"),
            ("m/b.sql", "SELECT 2"),
        ],
    )

    assert (sql_dir / "m.sql").read_text() == "SELECT 1;\n\nSELECT 2;"


def test_nested_subfolders_keep_their_full_path(client, sql_dir):
    response = upload(client, [("churn/eu/customer/base.sql", "SELECT 1")])

    assert response.get_json()["merged_files"] == ["churn/eu/customer.sql"]
    assert (sql_dir / "churn" / "eu" / "customer.sql").exists()


def test_loose_files_without_a_folder_fall_back(client, sql_dir):
    response = upload(client, [("a.sql", "SELECT 1"), ("b.sql", "SELECT 2")])

    assert response.get_json()["merged_files"] == ["merged_folder.sql"]
    assert (sql_dir / "merged_folder.sql").exists()


def test_existing_file_blocks_the_whole_upload(client, sql_dir):
    upload(client, [("churn/customer/a.sql", "SELECT 1")])

    response = upload(
        client,
        [
            ("churn/customer/a.sql", "SELECT 9"),
            ("churn/insurance/b.sql", "SELECT 2"),
        ],
    )

    assert response.status_code == 400
    assert "churn/customer.sql" in response.get_json()["error"]["user_message"]
    # Nothing was written - not even the non-conflicting subfolder
    assert not (sql_dir / "churn" / "insurance.sql").exists()
    assert "SELECT 9" not in (sql_dir / "churn" / "customer.sql").read_text()


def test_overwrite_replaces_existing_files(client, sql_dir):
    upload(client, [("churn/customer/a.sql", "SELECT 'old'")])

    response = upload(client, [("churn/customer/a.sql", "SELECT 'new'")], overwrite=True)

    assert response.status_code == 200
    assert "new" in (sql_dir / "churn" / "customer.sql").read_text()


def test_conflict_reports_details_the_ui_can_act_on(client, sql_dir):
    """The frontend prompts to overwrite off these fields rather than dead-ending."""
    upload(client, [("churn/customer/a.sql", "SELECT 1"), ("churn/insurance/b.sql", "SELECT 2")])

    response = upload(client, [("churn/customer/a.sql", "SELECT 9")])

    assert response.status_code == 400
    details = response.get_json()["error"]["details"]
    assert details["can_overwrite"] is True
    assert details["conflicts"] == ["churn/customer.sql"]


def test_single_file_conflict_reports_details(client, sql_dir):
    data = {"file": (BytesIO(b"SELECT 1"), "a.sql")}
    client.post("/api/upload", data=data, content_type="multipart/form-data")

    response = client.post(
        "/api/upload",
        data={"file": (BytesIO(b"SELECT 2"), "a.sql")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 409
    details = response.get_json()["error"]["details"]
    assert details["can_overwrite"] is True
    assert details["conflicts"] == ["a.sql"]


def test_single_file_overwrite_succeeds(client, sql_dir):
    client.post("/api/upload", data={"file": (BytesIO(b"SELECT 1"), "a.sql")}, content_type="multipart/form-data")

    response = client.post(
        "/api/upload?overwrite=true",
        data={"file": (BytesIO(b"SELECT 2"), "a.sql")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert (sql_dir / "a.sql").read_text() == "SELECT 2"


def test_delete_without_a_filename_is_a_clear_error(client):
    """Previously aborted with a 500 referencing a file that no longer exists."""
    response = client.delete("/api/delete-query/1")

    assert response.status_code == 400
    assert "filename" in response.get_json()["error"]["user_message"]


def test_delete_missing_file_is_a_404(client):
    response = client.delete("/api/delete-query/1?filename=nope.sql")

    assert response.status_code == 404


def test_empty_files_are_skipped_and_empty_groups_dropped(client, sql_dir):
    response = upload(
        client,
        [
            ("churn/customer/a.sql", "SELECT 1"),
            ("churn/empty/void.sql", "   "),
        ],
    )

    body = response.get_json()
    assert body["merged_files"] == ["churn/customer.sql"]
    assert not (sql_dir / "churn" / "empty.sql").exists()


def test_all_empty_is_an_error(client):
    response = upload(client, [("churn/a.sql", "  ")])

    assert response.status_code == 400
    assert "No valid SQL content" in response.get_json()["error"]["user_message"]


def test_non_sql_files_are_rejected(client):
    response = upload(client, [("churn/readme.txt", "hello")])

    assert response.status_code == 400


def test_path_traversal_is_rejected(client, sql_dir):
    response = upload(client, [("../evil.sql", "SELECT 1")])

    assert response.status_code == 400
    assert not (sql_dir.parent / "evil.sql").exists()


def test_colliding_table_names_in_one_subfolder_reject_the_upload(client, sql_dir):
    """Two files in one subfolder creating different tables with the same short name."""
    response = upload(
        client,
        [
            ("churn/customer/a.sql", "CREATE TABLE `proj.mart.base` AS SELECT 1"),
            ("churn/customer/b.sql", "CREATE TABLE `proj.scratch.base` AS SELECT 2"),
            ("churn/insurance/c.sql", "CREATE TABLE `proj.mart.claims` AS SELECT 3"),
        ],
    )

    assert response.status_code == 400
    assert "base" in response.get_json()["error"]["user_message"]
    # Nothing was written - not even the clean subfolder
    assert not (sql_dir / "churn" / "customer.sql").exists()
    assert not (sql_dir / "churn" / "insurance.sql").exists()


def test_same_short_name_in_different_subfolders_is_fine(client, sql_dir):
    """The per-subfolder split at work: separate queries, so no collision."""
    response = upload(
        client,
        [
            ("churn/customer/a.sql", "CREATE TABLE `proj.customer_mart.base` AS SELECT 1"),
            ("churn/insurance/b.sql", "CREATE TABLE `proj.insurance_mart.base` AS SELECT 2"),
        ],
    )

    assert response.status_code == 200
    assert response.get_json()["queries_created"] == 2


def test_nested_merged_files_can_be_deleted(client, sql_dir):
    upload(client, [("churn/customer/a.sql", "SELECT 1")])

    response = client.delete("/api/delete-query/1?filename=churn%2Fcustomer.sql")

    assert response.status_code == 200
    assert not (sql_dir / "churn" / "customer.sql").exists()


def test_delete_rejects_path_traversal(client, sql_dir):
    outside = sql_dir.parent / "outside.sql"
    outside.write_text("SELECT 1")

    response = client.delete("/api/delete-query/1?filename=..%2Foutside.sql")

    assert response.status_code == 400
    assert outside.exists()
