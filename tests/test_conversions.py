"""Tests for the conversion as the unit of work.

One uploaded folder is one conversion: one sidebar entry, one flow, one deploy. Its
subfolders are domains - outputs of the conversion rather than things to navigate
between.
"""

import pytest

from dbt_training_wheels.services import query_service
from dbt_training_wheels.services.query_service import conversion_name_for, get_conversion_for_query, load_conversions

PRODUCER = "CREATE OR REPLACE TABLE `proj.sandbox.customers_90d` AS SELECT 1 AS customer_id;"
CONSUMER = (
    "CREATE OR REPLACE TABLE `proj.ecomm.sku` AS\n"
    "SELECT a.article_id FROM `proj.dwh.items` a\n"
    "JOIN `proj.sandbox.customers_90d` c USING (customer_id);"
)
STANDALONE = "CREATE OR REPLACE TABLE `proj.other.lookup` AS SELECT 1;"


@pytest.fixture
def uploaded(tmp_path, monkeypatch):
    """Two uploads: a folder of two domains, and a lone file."""
    root = tmp_path / "source_sql_file"
    (root / "churn").mkdir(parents=True)
    (root / "churn" / "customer.sql").write_text(PRODUCER)
    (root / "churn" / "insurance.sql").write_text(CONSUMER)
    (root / "standalone.sql").write_text(STANDALONE)
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(root))
    return root


# ---------------------------------------------------------------- naming


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("churn/customer.sql", "churn"),
        ("churn/insurance.sql", "churn"),
        # A root-level file is its own conversion
        ("standalone.sql", "standalone"),
        ("churn/eu/claims.sql", "churn"),
        (None, ""),
    ],
)
def test_conversion_name_is_the_uploaded_folder(filename, expected):
    assert conversion_name_for(filename) == expected


# ---------------------------------------------------------------- grouping


def test_one_folder_is_one_conversion(uploaded):
    names = [m["name"] for m in load_conversions()]

    assert names == ["churn", "standalone"]


def test_a_conversion_carries_its_domains(uploaded):
    churn = next(m for m in load_conversions() if m["name"] == "churn")

    assert churn["domains"] == ["customer", "insurance"]
    assert len(churn["queries"]) == 2


def test_domains_are_in_deploy_order(uploaded):
    """insurance reads customer's table, so customer comes first."""
    churn = next(m for m in load_conversions() if m["name"] == "churn")

    assert churn["domains"] == ["customer", "insurance"]
    assert churn["primary_query_id"] == churn["queries"][0]["id"]


def test_a_lone_file_is_a_single_domain_conversion(uploaded):
    standalone = next(m for m in load_conversions() if m["name"] == "standalone")

    assert standalone["domains"] == ["standalone"]
    assert len(standalone["queries"]) == 1


def test_conversions_do_not_bleed_into_each_other(uploaded):
    churn = next(m for m in load_conversions() if m["name"] == "churn")

    assert all("churn/" in q["filename"] for q in churn["queries"])


def test_no_uploads_gives_no_conversions(tmp_path, monkeypatch):
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(tmp_path / "empty"))

    assert load_conversions() == []


# ---------------------------------------------------------------- lookup


def test_any_domain_resolves_back_to_its_conversion(uploaded):
    churn = next(m for m in load_conversions() if m["name"] == "churn")

    for query_id in churn["query_ids"]:
        assert get_conversion_for_query(query_id)["name"] == "churn"


def test_an_unknown_query_has_no_conversion(uploaded):
    assert get_conversion_for_query(999) is None
