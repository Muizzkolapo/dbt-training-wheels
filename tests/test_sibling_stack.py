"""Tests for deploying sibling queries as one stack.

Each subfolder of an upload is its own query and its own domain, so a stack can't live
inside one query - it spans the sibling queries of one folder, ordered so a query that
reads another's table merges after it.
"""

import pytest

from dbt_training_wheels.services import query_service
from dbt_training_wheels.services.domain_resolver import order_sibling_queries
from dbt_training_wheels.services.query_service import get_sibling_queries

PRODUCER = "CREATE OR REPLACE TABLE `proj.sandbox.customers_90d` AS SELECT 1 AS customer_id;"

# Reads the table the producer creates
CONSUMER = (
    "CREATE OR REPLACE TABLE `proj.ecomm.sku` AS\n"
    "SELECT a.article_id FROM `proj.dwh.items` a\n"
    "JOIN `proj.sandbox.customers_90d` c USING (customer_id);"
)

STANDALONE = "CREATE OR REPLACE TABLE `proj.other.lookup` AS SELECT 1;"


def _query(filename, sql, query_id=1):
    return {"id": query_id, "name": filename, "filename": filename, "sql": sql}


@pytest.fixture
def uploaded(tmp_path, monkeypatch):
    """A source_sql_file holding one folder upload with three subfolder queries."""
    root = tmp_path / "source_sql_file"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "sample1.sql").write_text(PRODUCER)
    (root / "demo" / "sample2.sql").write_text(CONSUMER)
    (root / "demo" / "sample3.sql").write_text(STANDALONE)
    # A different upload entirely - must never be treated as a sibling
    (root / "unrelated.sql").write_text(STANDALONE)
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(root))
    return root


# ---------------------------------------------------------------- discovery


def test_siblings_are_the_queries_from_the_same_upload(uploaded):
    siblings = get_sibling_queries({"filename": "demo/sample1.sql"})

    assert [s["filename"] for s in siblings] == [
        "demo/sample1.sql",
        "demo/sample2.sql",
        "demo/sample3.sql",
    ]


def test_a_root_level_query_has_no_siblings(uploaded):
    assert get_sibling_queries({"filename": "unrelated.sql"}) == []


def test_queries_from_other_folders_are_not_siblings(uploaded):
    siblings = get_sibling_queries({"filename": "demo/sample2.sql"})

    assert all(s["filename"].startswith("demo/") for s in siblings)
    assert "unrelated.sql" not in [s["filename"] for s in siblings]


# ---------------------------------------------------------------- ordering


def test_a_consumer_is_ordered_after_its_producer():
    queries = [_query("demo/sample2.sql", CONSUMER, 2), _query("demo/sample1.sql", PRODUCER, 1)]

    ordered = order_sibling_queries(queries)

    assert [q["filename"] for q in ordered] == ["demo/sample1.sql", "demo/sample2.sql"]


def test_unrelated_queries_keep_their_input_order():
    queries = [_query("demo/b.sql", STANDALONE, 1), _query("demo/a.sql", PRODUCER, 2)]

    ordered = order_sibling_queries(queries)

    assert [q["filename"] for q in ordered] == ["demo/b.sql", "demo/a.sql"]


def test_a_query_reading_its_own_table_is_not_self_dependent():
    """CREATE then read the same table within one query mustn't look like a dependency."""
    sql = PRODUCER + "\nCREATE OR REPLACE TABLE `proj.sandbox.next` AS SELECT * FROM `proj.sandbox.customers_90d`;"
    queries = [_query("demo/a.sql", sql, 1), _query("demo/b.sql", STANDALONE, 2)]

    ordered = order_sibling_queries(queries)

    assert len(ordered) == 2


def test_a_cycle_falls_back_to_input_order():
    a = "CREATE OR REPLACE TABLE `proj.d.a` AS SELECT * FROM `proj.d.b`;"
    b = "CREATE OR REPLACE TABLE `proj.d.b` AS SELECT * FROM `proj.d.a`;"
    queries = [_query("demo/a.sql", a, 1), _query("demo/b.sql", b, 2)]

    ordered = order_sibling_queries(queries)

    assert [q["filename"] for q in ordered] == ["demo/a.sql", "demo/b.sql"]


def test_a_chain_of_three_orders_end_to_end():
    first = "CREATE OR REPLACE TABLE `proj.d.one` AS SELECT 1;"
    second = "CREATE OR REPLACE TABLE `proj.d.two` AS SELECT * FROM `proj.d.one`;"
    third = "CREATE OR REPLACE TABLE `proj.d.three` AS SELECT * FROM `proj.d.two`;"
    queries = [
        _query("demo/third.sql", third, 3),
        _query("demo/first.sql", first, 1),
        _query("demo/second.sql", second, 2),
    ]

    ordered = order_sibling_queries(queries)

    assert [q["filename"] for q in ordered] == ["demo/first.sql", "demo/second.sql", "demo/third.sql"]
