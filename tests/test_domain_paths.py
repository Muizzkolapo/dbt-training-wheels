"""Tests for per-domain output paths.

Models are written to models/<domain>/, where the domain comes from the folder the
query was uploaded from. The conversion is not a path segment - it names the branch and
the pull request instead.
"""

import pytest

from dbt_training_wheels.services.analysis_service import analyze_query
from dbt_training_wheels.services.file_generator import generate_files_for_query

SINGLE_DOMAIN_SQL = (
    "CREATE OR REPLACE TABLE `proj.sandbox.customers_90d` AS\n"
    "SELECT customer_id FROM `proj.dwh.dim_customer` WHERE active = true;"
)

# Writes to two datasets, so config domains can split it within one query
CROSS_DOMAIN_SQL = (
    "CREATE OR REPLACE TABLE `proj.customer_mart.cust_base` AS\n"
    "SELECT id FROM `proj.raw.customers`;\n"
    "CREATE OR REPLACE TABLE `proj.insurance_mart.claims` AS\n"
    "SELECT c.id FROM `proj.customer_mart.cust_base` c JOIN `proj.raw.claims` r ON r.id = c.id;"
)


class FakeQueryConfig:
    """Minimal stand-in for QueryConfiguration's path-related fields."""

    def __init__(self, domain_area="typed_domain", model_group="group_a"):
        self.domain_area = domain_area
        self.model_group = model_group
        self.naming = None
        self.model_configurations = []
        self.cross_project_decisions = []

    @property
    def model_path(self):
        return f"{self.domain_area}/{self.model_group}"


def _generate(sql, filename, marts, config=None, query_config=None):
    query = {
        "id": 1,
        "name": "demo",
        "filename": filename,
        "sql": sql,
        "tables": marts,
        "insertCount": len(marts),
    }
    analysis = analyze_query(query, config, user_mart_selection=marts)
    return generate_files_for_query(
        query,
        analysis,
        config,
        query_config=query_config or FakeQueryConfig(),
        domain_area="typed_domain",
        model_group="group_a",
        user_mart_selection=marts,
    )


def _model_paths(files):
    return sorted(f["path"] for f in files if f["type"] == "model")


def test_models_are_written_under_their_domain():
    """demo/sample1.sql -> domain sample1 -> models/sample1/, with no conversion segment."""
    files = _generate(SINGLE_DOMAIN_SQL, "demo/sample1.sql", ["customers_90d"])

    paths = _model_paths(files)
    assert paths, "expected generated models"
    assert all(p.startswith("models/sample1/") for p in paths), paths
    # The conversion names the branch, not a directory
    assert not any("/demo/" in p for p in paths), paths
    # Nothing typed in the wizard decides the folder any more
    assert not any("typed_domain" in p or "/scv/" in p for p in paths), paths


def test_each_file_is_tagged_with_its_domain():
    """The tag is what routes files into stack entries, so it must be set."""
    files = _generate(SINGLE_DOMAIN_SQL, "demo/sample1.sql", ["customers_90d"])

    assert {f.get("domain") for f in files if f["type"] == "model"} == {"sample1"}


def test_schema_and_docs_sit_with_their_models():
    files = _generate(SINGLE_DOMAIN_SQL, "demo/sample1.sql", ["customers_90d"])

    schema = [f["path"] for f in files if f["path"].endswith("schema.yml")]
    docs = [f["path"] for f in files if f["type"] == "docs"]

    assert schema and all(p.startswith("models/sample1/") for p in schema), schema
    # The docs file is still named after the conversion, so two conversions sharing a
    # domain folder don't overwrite each other's docs
    assert docs == ["models/sample1/demo.md"], docs


def test_sources_sit_at_the_models_root_not_inside_a_domain():
    """Every domain reads them, so they can't live in one domain's folder."""
    files = _generate(SINGLE_DOMAIN_SQL, "demo/sample1.sql", ["customers_90d"])

    sources = [f["path"] for f in files if f["path"].endswith("sources.yml")]
    assert sources == ["models/sources.yml"], sources


def test_sources_ship_in_the_first_domains_branch():
    """The bottom of the stack, so every later branch inherits them before parsing."""
    files = _generate(SINGLE_DOMAIN_SQL, "demo/sample1.sql", ["customers_90d"])

    sources = [f for f in files if f["path"].endswith("sources.yml")]
    assert sources and sources[0]["domain"] == "sample1"


def test_a_flat_upload_is_its_own_domain():
    """A lone file names the domain; there is no second level to add."""
    files = _generate(SINGLE_DOMAIN_SQL, "lone.sql", ["customers_90d"])

    paths = _model_paths(files)
    assert all(p.startswith("models/lone/") for p in paths), paths
    assert not any("models/lone/lone/" in p for p in paths), paths


def test_config_domains_split_paths_within_one_query():
    """The override case: one folder that genuinely spans domains."""
    from dbt_training_wheels.config_schema import load_config_from_dict

    config = load_config_from_dict(
        {
            "org_name": "test",
            "projects": {
                "p": {
                    "dbt_config": {
                        "domains": {
                            "customer": {"datasets": ["customer_mart"]},
                            "insurance": {"datasets": ["insurance_mart"]},
                        }
                    }
                }
            },
        }
    )

    query = {
        "id": 1,
        "name": "demo",
        "filename": "demo/mixed.sql",
        "sql": CROSS_DOMAIN_SQL,
        "tables": ["cust_base", "claims"],
        "insertCount": 2,
    }
    analysis = analyze_query(query, config, project_name="p", user_mart_selection=["claims"])
    files = generate_files_for_query(
        query,
        analysis,
        config,
        project_name="p",
        query_config=FakeQueryConfig(),
        domain_area="typed_domain",
        model_group="group_a",
        user_mart_selection=["claims"],
    )

    paths = _model_paths(files)
    # Config overrides the domain derived from the folder; still no conversion segment
    assert any(p.startswith("models/customer/") for p in paths), paths
    assert any(p.startswith("models/insurance/") for p in paths), paths
    assert not any("/demo/" in p for p in paths), paths


# ------------------------------------------------------------------ conversion tag
#
# Paths stop at the domain, so nothing in the layout says which conversion a model came
# from. The tag does, which is how you select one conversion's models in dbt.


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("demo/sample1.sql", "conversion_demo"),
        ("lone.sql", "conversion_lone"),
        ("Churn EU/customer.sql", "conversion_churn_eu"),
        ("", ""),
        (None, ""),
    ],
)
def test_conversion_tag_is_derived_from_the_folder(filename, expected):
    from dbt_training_wheels.services.query_service import conversion_tag_for

    assert conversion_tag_for(filename) == expected


def test_every_generated_model_carries_the_conversion_tag():
    """If one model misses the tag, the DAG silently stops running it."""
    files = _generate(SINGLE_DOMAIN_SQL, "demo/sample1.sql", ["customers_90d"])

    models = [f for f in files if f["type"] == "model"]
    assert models, "expected generated models"
    missing = [f["path"] for f in models if "conversion_demo" not in f["content"]]
    assert not missing, missing


def test_the_tag_is_added_to_existing_tags_not_instead_of_them():
    """A model with its own tags still has to be reachable by the DAG."""
    from dbt_training_wheels.services.file_generator import generate_final_model_content

    content = generate_final_model_content(
        "customers_90d",
        "demo",
        "SELECT 1",
        None,
        {"materialization": "table", "tags": ["daily", "pii"]},
        conversion_tag="conversion_demo",
    )

    assert "'daily'" in content and "'pii'" in content
    assert "'conversion_demo'" in content


def test_the_tag_spans_domains():
    """One selector covers a conversion however many domains it touches."""
    from dbt_training_wheels.services.query_service import conversion_tag_for

    assert conversion_tag_for("churn/customer.sql") == conversion_tag_for("churn/insurance.sql")
