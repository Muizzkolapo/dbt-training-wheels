"""Tests for domain attribution.

Domains decide how a conversion is split into a stack of PRs, so these cover both the
dataset -> domain lookup and the ordering between domains.
"""

import pytest

from dbt_training_wheels.config_schema import load_config_from_dict
from dbt_training_wheels.services.domain_resolver import (
    UNASSIGNED_DOMAIN,
    DomainResolver,
    attribute_models_to_domains,
    domain_from_filename,
    group_files_by_domain,
)
from dbt_training_wheels.utils.sql_parser import extract_destination_datasets

NAMING = {
    "stagingModelPrefix": "stg__",
    "intermediateModelPrefix": "int__",
    "martModelPrefix": "mart__",
}


def make_config(domains=None):
    """Build an OrganizationConfig with domains for the 'reporting' project."""
    dbt_config = {}
    if domains is not None:
        dbt_config["domains"] = domains

    return load_config_from_dict(
        {
            "org_name": "test",
            "projects": {"reporting": {"dbt_config": dbt_config}},
        }
    )


def make_analysis(models):
    """Build an analysis-result shaped dict.

    Args:
        models: list of (layer, name, dataset, sql) tuples
    """
    layers = {"staging": [], "intermediate": [], "mart": []}
    for layer, name, dataset, sql in models:
        layers[layer].append({"name": name, "dataset": dataset, "transformedSql": sql})
    return {"naming": NAMING, "layerClassification": layers}


TWO_DOMAINS = {
    "customer": {"datasets": ["customer_mart"]},
    "insurance": {"datasets": ["insurance_mart"]},
}


# ---------------------------------------------------------------- config parsing


def test_domains_are_parsed_from_config():
    config = make_config(TWO_DOMAINS)

    domains = config.projects["reporting"].dbt_config.domains

    assert {d.name for d in domains} == {"customer", "insurance"}
    assert next(d for d in domains if d.name == "customer").datasets == ["customer_mart"]


def test_missing_domains_key_is_empty():
    assert make_config().projects["reporting"].dbt_config.domains == []


# ---------------------------------------------------------------- resolver


def test_resolver_maps_datasets_to_domains():
    resolver = DomainResolver(make_config(TWO_DOMAINS), "reporting")

    assert resolver.is_configured
    assert resolver.resolve("customer_mart") == "customer"
    assert resolver.resolve("insurance_mart") == "insurance"
    assert resolver.resolve("something_else") is None
    assert resolver.resolve("") is None


def test_resolver_is_case_insensitive():
    resolver = DomainResolver(make_config({"customer": {"datasets": ["Customer_Mart"]}}), "reporting")

    assert resolver.resolve("customer_mart") == "customer"
    assert resolver.resolve("CUSTOMER_MART") == "customer"


def test_resolver_is_unconfigured_for_unknown_project():
    resolver = DomainResolver(make_config(TWO_DOMAINS), "some_other_project")

    assert not resolver.is_configured
    assert resolver.resolve("customer_mart") is None


def test_first_domain_wins_when_a_dataset_is_claimed_twice():
    config = make_config(
        {
            "customer": {"datasets": ["shared_mart"]},
            "insurance": {"datasets": ["shared_mart"]},
        }
    )

    assert DomainResolver(config, "reporting").resolve("shared_mart") == "customer"


# ---------------------------------------------------------------- folder-derived domain


@pytest.mark.parametrize(
    "filename,expected",
    [
        # A folder you upload is one domain
        ("demo.sql", "demo"),
        # Subfolders within it are their own domains
        ("churn/customer.sql", "customer"),
        ("churn/insurance.sql", "insurance"),
        ("churn/eu/claims.sql", "claims"),
        ("DEMO.SQL", "DEMO"),
        (None, ""),
        ("", ""),
    ],
)
def test_domain_comes_from_the_folder_structure(filename, expected):
    assert domain_from_filename(filename) == expected


def test_folder_domain_needs_no_config():
    """The headline case: no domains configured, domain still resolves from the folder."""
    analysis = make_analysis([("intermediate", "cust_base", "sandbox", "select 1")])

    groups = attribute_models_to_domains(analysis, make_config(), "reporting", query_filename="demo.sql")

    assert [g.domain for g in groups] == ["demo"]
    assert groups[0].models[0].source == "folder"


def test_sibling_subfolders_are_different_domains():
    analysis = make_analysis([("intermediate", "a", "ds", "select 1")])

    customer = attribute_models_to_domains(analysis, make_config(), query_filename="churn/customer.sql")
    insurance = attribute_models_to_domains(analysis, make_config(), query_filename="churn/insurance.sql")

    assert [g.domain for g in customer] == ["customer"]
    assert [g.domain for g in insurance] == ["insurance"]


def test_configured_datasets_override_the_folder():
    """Config is the escape hatch for a folder that genuinely spans domains."""
    analysis = make_analysis(
        [
            ("intermediate", "cust", "customer_mart", "select 1"),
            ("intermediate", "claims", "insurance_mart", "select 2"),
        ]
    )

    groups = attribute_models_to_domains(
        analysis, make_config(TWO_DOMAINS), "reporting", query_filename="demo.sql"
    )

    assert sorted(g.domain for g in groups) == ["customer", "insurance"]


def test_folder_domain_beats_the_generic_fallback():
    analysis = make_analysis([("intermediate", "a", "unknown_ds", "select 1")])

    groups = attribute_models_to_domains(
        analysis, make_config(), fallback_domain="typed_by_user", query_filename="demo.sql"
    )

    assert [g.domain for g in groups] == ["demo"]


# ---------------------------------------------------------------- attribution


def test_single_domain_produces_one_group():
    analysis = make_analysis(
        [
            ("intermediate", "cust_base", "customer_mart", "select 1"),
            ("mart", "cust_base", "customer_mart", ""),
        ]
    )

    groups = attribute_models_to_domains(analysis, make_config(TWO_DOMAINS), "reporting")

    assert len(groups) == 1
    assert groups[0].domain == "customer"
    assert groups[0].layers == ["intermediate", "mart"]


def test_models_split_across_domains():
    analysis = make_analysis(
        [
            ("intermediate", "cust_base", "customer_mart", "select 1"),
            ("intermediate", "claims", "insurance_mart", "select 2"),
        ]
    )

    groups = attribute_models_to_domains(analysis, make_config(TWO_DOMAINS), "reporting")

    assert [g.domain for g in groups] == ["customer", "insurance"]
    assert groups[0].model_names == ["cust_base"]
    assert groups[1].model_names == ["claims"]


def test_domains_are_ordered_by_dependency():
    """A domain that refs another must come after it, whatever order it appears in."""
    analysis = make_analysis(
        [
            # insurance is declared first but depends on customer
            ("intermediate", "claims", "insurance_mart", "select * from {{ ref('int__cust_base') }}"),
            ("intermediate", "cust_base", "customer_mart", "select 1"),
        ]
    )

    groups = attribute_models_to_domains(analysis, make_config(TWO_DOMAINS), "reporting")

    assert [g.domain for g in groups] == ["customer", "insurance"]


def test_circular_domains_fall_back_to_declaration_order():
    analysis = make_analysis(
        [
            ("intermediate", "cust_base", "customer_mart", "select * from {{ ref('int__claims') }}"),
            ("intermediate", "claims", "insurance_mart", "select * from {{ ref('int__cust_base') }}"),
        ]
    )

    groups = attribute_models_to_domains(analysis, make_config(TWO_DOMAINS), "reporting")

    assert [g.domain for g in groups] == ["customer", "insurance"]


def test_unknown_datasets_use_the_fallback_domain():
    analysis = make_analysis([("intermediate", "orphan", "mystery_dataset", "select 1")])

    groups = attribute_models_to_domains(
        analysis, make_config(TWO_DOMAINS), "reporting", fallback_domain="other"
    )

    assert [g.domain for g in groups] == ["other"]
    assert groups[0].models[0].source == "fallback"


def test_no_configured_domains_gives_a_single_unassigned_group():
    analysis = make_analysis(
        [
            ("intermediate", "a", "customer_mart", "select 1"),
            ("intermediate", "b", "insurance_mart", "select 2"),
        ]
    )

    groups = attribute_models_to_domains(analysis, make_config(), "reporting")

    assert len(groups) == 1
    assert groups[0].domain == UNASSIGNED_DOMAIN
    assert groups[0].model_names == ["a", "b"]


def test_empty_analysis_gives_no_groups():
    assert attribute_models_to_domains({}, make_config(TWO_DOMAINS), "reporting") == []


# ---------------------------------------------------------------- file grouping


def _file(path, file_type):
    return {"path": path, "type": file_type, "content": ""}


CROSS_DOMAIN_ANALYSIS = make_analysis(
    [
        ("intermediate", "cust_base", "customer_mart", "select 1"),
        ("intermediate", "claims", "insurance_mart", "select * from {{ ref('int__cust_base') }}"),
        ("mart", "claims", "insurance_mart", ""),
    ]
)

GENERATED_FILES = [
    _file("models/sources.yml", "config"),
    _file("models/intermediate/int__cust_base.sql", "model"),
    _file("models/intermediate/int__claims.sql", "model"),
    _file("models/mart/mart__claims.sql", "model"),
    _file("models/intermediate/schema.yml", "config"),
    _file("models/churn.md", "docs"),
]


def _paths(bucket):
    return [f["path"] for f in bucket]


def test_model_files_follow_their_domain():
    groups = attribute_models_to_domains(CROSS_DOMAIN_ANALYSIS, make_config(TWO_DOMAINS), "reporting")

    customer, insurance = group_files_by_domain(GENERATED_FILES, groups, CROSS_DOMAIN_ANALYSIS)

    assert "models/intermediate/int__cust_base.sql" in _paths(customer)
    assert "models/intermediate/int__claims.sql" in _paths(insurance)
    assert "models/mart/mart__claims.sql" in _paths(insurance)


def test_sources_land_in_the_first_bucket():
    """Models can't parse without their sources, so sources.yml has to merge first."""
    groups = attribute_models_to_domains(CROSS_DOMAIN_ANALYSIS, make_config(TWO_DOMAINS), "reporting")

    customer, insurance = group_files_by_domain(GENERATED_FILES, groups, CROSS_DOMAIN_ANALYSIS)

    assert "models/sources.yml" in _paths(customer)
    assert "models/sources.yml" not in _paths(insurance)


def test_docs_and_schema_land_in_the_last_bucket():
    """They describe every model in the conversion, so they can't merge before all exist."""
    groups = attribute_models_to_domains(CROSS_DOMAIN_ANALYSIS, make_config(TWO_DOMAINS), "reporting")

    customer, insurance = group_files_by_domain(GENERATED_FILES, groups, CROSS_DOMAIN_ANALYSIS)

    assert "models/intermediate/schema.yml" in _paths(insurance)
    assert "models/churn.md" in _paths(insurance)
    assert "models/intermediate/schema.yml" not in _paths(customer)


def test_unattributed_model_files_go_last():
    groups = attribute_models_to_domains(CROSS_DOMAIN_ANALYSIS, make_config(TWO_DOMAINS), "reporting")
    files = [*GENERATED_FILES, _file("models/intermediate/int__mystery.sql", "model")]

    customer, insurance = group_files_by_domain(files, groups, CROSS_DOMAIN_ANALYSIS)

    assert "models/intermediate/int__mystery.sql" in _paths(insurance)


def test_every_file_is_placed_exactly_once():
    groups = attribute_models_to_domains(CROSS_DOMAIN_ANALYSIS, make_config(TWO_DOMAINS), "reporting")

    buckets = group_files_by_domain(GENERATED_FILES, groups, CROSS_DOMAIN_ANALYSIS)

    placed = [path for bucket in buckets for path in _paths(bucket)]
    assert sorted(placed) == sorted(f["path"] for f in GENERATED_FILES)


def test_no_groups_gives_no_buckets():
    assert group_files_by_domain(GENERATED_FILES, [], CROSS_DOMAIN_ANALYSIS) == []


# ---------------------------------------------------------------- destination datasets


@pytest.mark.parametrize(
    "sql,expected",
    [
        (
            "CREATE OR REPLACE TABLE `proj.customer_mart.cust_base` AS SELECT 1",
            {"cust_base": {"fullName": "proj.customer_mart.cust_base", "project": "proj", "dataset": "customer_mart"}},
        ),
        (
            "INSERT INTO `proj.insurance_mart.claims` SELECT 1",
            {"claims": {"fullName": "proj.insurance_mart.claims", "project": "proj", "dataset": "insurance_mart"}},
        ),
        (
            "CREATE TABLE dataset_only.tbl AS SELECT 1",
            {"tbl": {"fullName": "dataset_only.tbl", "project": "", "dataset": "dataset_only"}},
        ),
        ("SELECT 1", {}),
    ],
)
def test_extract_destination_datasets(sql, expected):
    assert extract_destination_datasets(sql) == expected


def test_destination_datasets_keeps_the_first_write():
    sql = """
    CREATE OR REPLACE TABLE `proj.customer_mart.tbl` AS SELECT 1;
    INSERT INTO `proj.scratch.tbl` SELECT 2;
    """

    assert extract_destination_datasets(sql)["tbl"]["dataset"] == "customer_mart"


# ---------------------------------------------------------------- end to end

CROSS_DOMAIN_SQL = """
CREATE OR REPLACE TABLE `proj.customer_mart.cust_base` AS
SELECT id, name FROM `proj.raw.customers` WHERE active = true;

CREATE OR REPLACE TABLE `proj.insurance_mart.claims_summary` AS
SELECT c.id, SUM(cl.amount) AS total
FROM `proj.customer_mart.cust_base` c
JOIN `proj.raw.claims` cl ON cl.customer_id = c.id
GROUP BY c.id;
"""


def test_analysis_carries_datasets_through_to_domain_groups():
    """The full path: analyze SQL, then split the resulting models by domain."""
    from dbt_training_wheels.services.analysis_service import analyze_query

    config = make_config(TWO_DOMAINS)
    query = {
        "name": "churn",
        "sql": CROSS_DOMAIN_SQL,
        "tables": ["cust_base", "claims_summary"],
        "insertCount": 2,
    }

    results = analyze_query(
        query,
        config,
        project_name="reporting",
        user_mart_selection=["claims_summary"],
    )

    # Every structural model knows the dataset it writes to
    datasets = {
        component["name"]: component["dataset"]
        for layer in ("staging", "intermediate")
        for component in results["layerClassification"][layer]
    }
    assert datasets == {"cust_base": "customer_mart", "claims_summary": "insurance_mart"}

    groups = attribute_models_to_domains(results, config, "reporting")

    # claims_summary reads cust_base, so customer has to merge first
    assert [g.domain for g in groups] == ["customer", "insurance"]
    assert groups[0].model_names == ["cust_base"]
    assert "claims_summary" in groups[1].model_names
