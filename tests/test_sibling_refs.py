"""Tests for sibling references between subfolder queries.

Each subfolder of an upload is its own query - but they're one conversion
into one dbt project, so a table one subfolder creates and another reads must become
a ref() call, not a source() call.
"""

import pytest

from dbt_training_wheels.services import query_service
from dbt_training_wheels.services.analysis_service import analyze_query
from dbt_training_wheels.services.file_generator import generate_sources_yml
from dbt_training_wheels.services.query_service import get_sibling_created_tables
from dbt_training_wheels.utils.sql_parser import analyze_sql_content

CUSTOMER_SQL = "CREATE OR REPLACE TABLE `proj.customer_mart.cust_base` AS\nSELECT 1 AS id FROM `proj.raw.customers`;"

INSURANCE_SQL = (
    "CREATE OR REPLACE TABLE `proj.insurance_mart.claims_summary` AS\n"
    "SELECT c.id, cl.amount\n"
    "FROM `proj.customer_mart.cust_base` c\n"
    "JOIN `proj.raw.claims` cl ON cl.id = c.id;"
)


@pytest.fixture
def sql_dir(tmp_path, monkeypatch):
    """A source_sql_file directory holding the churn upload's two merged queries."""
    root = tmp_path / "source_sql_file"
    (root / "churn").mkdir(parents=True)
    (root / "churn" / "customer.sql").write_text(CUSTOMER_SQL)
    (root / "churn" / "insurance.sql").write_text(INSURANCE_SQL)
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(root))
    return root


# ---------------------------------------------------------------- sibling scan


def test_siblings_are_the_other_queries_in_the_same_folder(sql_dir):
    tables = get_sibling_created_tables({"filename": "churn/insurance.sql"})

    assert tables == {"cust_base"}


def test_own_tables_are_not_siblings(sql_dir):
    tables = get_sibling_created_tables({"filename": "churn/customer.sql"})

    assert "cust_base" not in tables
    assert tables == {"claims_summary"}


def test_root_level_files_have_no_siblings(sql_dir):
    (sql_dir / "lone.sql").write_text(CUSTOMER_SQL)

    assert get_sibling_created_tables({"filename": "lone.sql"}) == set()


def test_missing_filename_has_no_siblings(sql_dir):
    assert get_sibling_created_tables({}) == set()


# ---------------------------------------------------------------- analysis


def test_sibling_reference_is_flagged_and_ref_suggested():
    _, hardcoded = analyze_sql_content(INSURANCE_SQL, sibling_tables={"cust_base"})

    sibling = next(t for t in hardcoded if t["sourceTable"] == "cust_base")
    assert sibling["isSiblingReference"] is True
    assert sibling["isSelfReference"] is True  # inherits self-ref treatment downstream
    assert sibling["suggestedRef"] == "{{ ref('int__cust_base') }}"

    # The genuinely external table is untouched
    external = next(t for t in hardcoded if t["sourceTable"] == "claims")
    assert external.get("isSiblingReference") is False
    assert external["isSelfReference"] is False


def test_sibling_matching_is_case_insensitive():
    _, hardcoded = analyze_sql_content(INSURANCE_SQL, sibling_tables={"CUST_BASE"})

    assert next(t for t in hardcoded if t["sourceTable"] == "cust_base")["isSiblingReference"] is True


def test_analyze_query_converts_sibling_to_ref(sql_dir):
    query = {
        "id": 1,
        "name": "insurance",
        "filename": "churn/insurance.sql",
        "sql": INSURANCE_SQL,
        "tables": ["claims_summary"],
        "insertCount": 1,
    }

    results = analyze_query(query, None, user_mart_selection=["claims_summary"])

    sql = results["finalTableSqls"][0]["sql"]
    assert "ref('int__cust_base')" in sql
    assert "source('customer_mart'" not in sql
    # The truly external table still becomes a source
    assert "source('raw', 'claims')" in sql


def test_without_sibling_context_it_stays_a_source():
    """The same SQL analyzed standalone (no folder) keeps the old behaviour."""
    query = {
        "id": 1,
        "name": "insurance",
        "sql": INSURANCE_SQL,
        "tables": ["claims_summary"],
        "insertCount": 1,
    }

    results = analyze_query(query, None, user_mart_selection=["claims_summary"])

    sql = results["finalTableSqls"][0]["sql"]
    assert "source('customer_mart', 'cust_base')" in sql
    assert "ref('int__cust_base')" not in sql


def test_sibling_tables_stay_out_of_sources_yml(sql_dir):
    query = {
        "id": 1,
        "name": "insurance",
        "filename": "churn/insurance.sql",
        "sql": INSURANCE_SQL,
        "tables": ["claims_summary"],
        "insertCount": 1,
    }
    results = analyze_query(query, None, user_mart_selection=["claims_summary"])

    sources_yml = generate_sources_yml(results)

    assert "cust_base" not in sources_yml
    assert "claims" in sources_yml  # the real external source is still declared
