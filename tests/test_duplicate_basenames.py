"""Tests for duplicate table basename detection.

The pipeline keys models on the short table name, so two different tables sharing one
short name would collapse into a single model built from whichever statement comes
first. That must fail loudly - at upload when the collision comes from merging, and at
analysis for scripts that arrived any other way.
"""

import pytest

from dbt_training_wheels.exceptions import AnalysisError
from dbt_training_wheels.services.analysis_service import analyze_query, detect_tables_for_query
from dbt_training_wheels.utils.sql_parser import find_conflicting_table_basenames, find_recreated_tables

CONFLICTING_SQL = (
    "CREATE OR REPLACE TABLE `proj.customer_mart.base` AS SELECT 1 AS id;\n"
    "CREATE OR REPLACE TABLE `proj.scratch.base` AS SELECT 2 AS id;"
)


# ---------------------------------------------------------------- detector


def test_different_datasets_same_short_name_is_a_conflict():
    conflicts = find_conflicting_table_basenames(CONFLICTING_SQL)

    assert conflicts == {"base": ["proj.customer_mart.base", "proj.scratch.base"]}


def test_different_projects_same_dataset_is_a_conflict():
    sql = "CREATE TABLE `proj1.mart.base` AS SELECT 1;\nCREATE TABLE `proj2.mart.base` AS SELECT 2;"

    assert "base" in find_conflicting_table_basenames(sql)


def test_same_table_written_twice_is_not_a_conflict():
    """CREATE then INSERT INTO the same target is the normal pattern."""
    sql = "CREATE OR REPLACE TABLE `proj.mart.base` AS SELECT 1;\nINSERT INTO `proj.mart.base` SELECT 2;"

    assert find_conflicting_table_basenames(sql) == {}


def test_shorter_qualification_of_the_same_table_is_not_a_conflict():
    """'mart.base' is treated as the same table as 'proj.mart.base'."""
    sql = "CREATE TABLE `proj.mart.base` AS SELECT 1;\nINSERT INTO `mart.base` SELECT 2;"

    assert find_conflicting_table_basenames(sql) == {}


def test_unqualified_writes_cannot_prove_a_conflict():
    sql = "CREATE TABLE `proj.mart.base` AS SELECT 1;\nINSERT INTO base SELECT 2;"

    assert find_conflicting_table_basenames(sql) == {}


def test_matching_is_case_insensitive():
    sql = "CREATE TABLE `proj.mart.Base` AS SELECT 1;\nCREATE TABLE `proj.scratch.BASE` AS SELECT 2;"

    assert "base" in find_conflicting_table_basenames(sql)


def test_distinct_names_are_not_conflicts():
    sql = "CREATE TABLE `proj.mart.customers` AS SELECT 1;\nCREATE TABLE `proj.mart.orders` AS SELECT 2;"

    assert find_conflicting_table_basenames(sql) == {}


# ---------------------------------------------------------------- analysis gates


def _query(sql):
    return {"id": 1, "name": "test", "sql": sql, "tables": ["base"], "insertCount": 2}


def test_analyze_query_refuses_conflicting_scripts():
    with pytest.raises(AnalysisError) as exc:
        analyze_query(_query(CONFLICTING_SQL), None, user_mart_selection=["base"])

    assert "base" in exc.value.user_message
    assert "proj.customer_mart.base" in exc.value.technical_message
    assert "proj.scratch.base" in exc.value.technical_message


def test_detect_tables_refuses_conflicting_scripts():
    """The mart-selection modal must never offer two entries backed by the same SQL."""
    with pytest.raises(AnalysisError) as exc:
        detect_tables_for_query(_query(CONFLICTING_SQL), None)

    assert "base" in exc.value.user_message


# ------------------------------------------------- same table built twice

# Two uploaded files each rebuilding one table - the shape that slipped through first
RECREATED_SQL = (
    "create or replace table my-gcp-project.sandbox.active_customers as SELECT 1;\n"
    "create or replace table my-gcp-project.sandbox.active_customers as SELECT 2;\n"
    "CREATE OR REPLACE TABLE `my-gcp-project.recommendations.sku_join` as SELECT 3;"
)


def test_same_table_created_twice_is_reported():
    """Not a name collision - the same target, so extraction and BigQuery disagree."""
    recreated = find_recreated_tables(RECREATED_SQL)

    assert recreated == {"my-gcp-project.sandbox.active_customers": 2}


def test_create_then_insert_is_still_allowed():
    """Build-then-append remains the normal pattern."""
    sql = "CREATE OR REPLACE TABLE `proj.mart.base` AS SELECT 1;\nINSERT INTO `proj.mart.base` SELECT 2;"

    assert find_recreated_tables(sql) == {}


def test_recreated_detection_is_case_insensitive():
    sql = "create or replace table `proj.mart.Base` as SELECT 1;\nCREATE OR REPLACE TABLE `PROJ.MART.BASE` AS SELECT 2;"

    assert find_recreated_tables(sql)


def test_recreated_tables_are_refused_by_analysis():
    query = {"id": 1, "name": "demo", "sql": RECREATED_SQL, "tables": ["active_customers"], "insertCount": 3}

    with pytest.raises(AnalysisError) as exc:
        analyze_query(query, None, user_mart_selection=["active_customers"])

    assert "active_customers" in exc.value.user_message
    assert "created 2 times" in exc.value.technical_message


def test_recreated_tables_are_refused_by_table_detection():
    query = {"id": 1, "name": "demo", "sql": RECREATED_SQL, "tables": ["active_customers"], "insertCount": 3}

    with pytest.raises(AnalysisError):
        detect_tables_for_query(query, None)


def test_clean_scripts_still_analyze():
    sql = "CREATE OR REPLACE TABLE `proj.mart.customers` AS SELECT 1 AS id FROM `proj.raw.src`;"
    query = {"id": 1, "name": "test", "sql": sql, "tables": ["customers"], "insertCount": 1}

    results = analyze_query(query, None, user_mart_selection=["customers"])

    assert results["layerClassification"]
