"""Tests for analysing a conversion rather than a single query.

A conversion is the unit of work, so one analysis covers every domain: one layer
classification, every table, and components tagged with the domain they belong to.
"""

import pytest

from dbt_training_wheels.services import query_service
from dbt_training_wheels.services.analysis_service import analyze_conversion
from dbt_training_wheels.services.query_service import load_conversions

CUSTOMER = (
    "CREATE OR REPLACE TABLE `proj.customer_mart.cust_base` AS\n"
    "SELECT id, name FROM `proj.raw.customers` WHERE active = true;"
)

# Reads the customer domain's table, so the domains are genuinely linked
INSURANCE = (
    "CREATE OR REPLACE TABLE `proj.insurance_mart.claims` AS\n"
    "SELECT c.id, SUM(r.amount) AS total\n"
    "FROM `proj.customer_mart.cust_base` c\n"
    "JOIN `proj.raw.claims` r ON r.customer_id = c.id\n"
    "GROUP BY c.id;"
)


@pytest.fixture
def conversion(tmp_path, monkeypatch):
    root = tmp_path / "source_sql_file"
    (root / "churn").mkdir(parents=True)
    (root / "churn" / "customer.sql").write_text(CUSTOMER)
    (root / "churn" / "insurance.sql").write_text(INSURANCE)
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(root))
    return next(m for m in load_conversions() if m["name"] == "churn")


def _models(results):
    """Every model in the merged result, as {name: domain}."""
    return {
        component["name"]: component["domain"]
        for layer in ("staging", "intermediate", "mart")
        for component in results["layerClassification"][layer]
    }


def test_one_analysis_covers_every_domain(conversion):
    results = analyze_conversion(conversion, None)

    models = _models(results)
    assert "cust_base" in models
    assert "claims" in models


def test_components_are_tagged_with_their_domain(conversion):
    """This tag is what lets the UI group by domain without a second request."""
    results = analyze_conversion(conversion, None)

    models = _models(results)
    assert models["cust_base"] == "customer"
    assert models["claims"] == "insurance"


def test_the_domains_are_listed_in_deploy_order(conversion):
    results = analyze_conversion(conversion, None)

    assert results["domains"] == ["customer", "insurance"]


def test_a_cross_domain_read_becomes_a_ref(conversion):
    """What makes one lineage graph possible: the link is a ref(), not a source()."""
    results = analyze_conversion(conversion, None)

    claims_sql = next(item["sql"] for item in results["finalTableSqls"] if item["table"] == "claims")
    assert "ref('int__cust_base')" in claims_sql
    assert "source('customer_mart'" not in claims_sql


def test_external_sources_are_still_sources(conversion):
    results = analyze_conversion(conversion, None)

    sources = {t["sourceTable"] for t in results["hardcodedTables"] if not t.get("isSelfReference")}
    assert {"customers", "claims"} & sources


def test_mart_selection_is_split_across_domains(conversion):
    """A flat selection is routed to whichever domain creates each table."""
    results = analyze_conversion(conversion, None, user_mart_selection=["claims"])

    marts = {c["name"]: c["domain"] for c in results["layerClassification"]["mart"]}
    assert marts == {"claims": "insurance"}


def test_selecting_marts_in_both_domains(conversion):
    results = analyze_conversion(conversion, None, user_mart_selection=["cust_base", "claims"])

    marts = {c["name"]: c["domain"] for c in results["layerClassification"]["mart"]}
    assert marts == {"cust_base": "customer", "claims": "insurance"}


def test_counts_are_summed_across_domains(conversion):
    results = analyze_conversion(conversion, None)

    assert results["modelsToCreate"] == 2


def test_naming_is_carried_through(conversion):
    """getAllModels() needs the prefixes to build model names."""
    results = analyze_conversion(conversion, None)

    assert results["naming"]["intermediateModelPrefix"] == "int__"


def test_per_domain_results_are_not_duplicated_into_the_payload(conversion):
    """Analysis results go into sessionStorage - don't carry two copies of everything."""
    results = analyze_conversion(conversion, None)

    assert "byDomain" not in results


def test_an_empty_conversion_analyses_to_nothing():
    assert analyze_conversion({"queries": []}, None) == {}
