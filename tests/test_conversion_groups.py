"""Tests for splitting an upload into groups that feed off each other.

An upload of subfolders usually isn't one piece of work. Subfolders that read each
other's tables have to merge in order; subfolders that share nothing shouldn't be made
to queue behind each other. Grouping is by connected component over that dependency,
which these pin - including the transitive case, where two subfolders that touch
nothing directly still belong together via a third.
"""

import pytest

from dbt_training_wheels.services import query_service
from dbt_training_wheels.services.domain_resolver import group_related_queries
from dbt_training_wheels.services.query_service import load_conversions


def sql_creating(table, reads=None):
    """A CREATE for `table`, optionally selecting from tables another query creates."""
    joins = "".join(f"\nJOIN `proj.sandbox.{r}` {r[:2]} USING (id)" for r in (reads or []))
    return f"CREATE OR REPLACE TABLE `proj.sandbox.{table}` AS\nSELECT id FROM `proj.dwh.seed`{joins};"


def _query(name, sql, query_id=1):
    return {"id": query_id, "name": name, "filename": f"churn/{name}.sql", "sql": sql}


def _names(groups):
    return [[q["name"] for q in group] for group in groups]


# ---------------------------------------------------------------- grouping


def test_unrelated_subfolders_are_separate_groups():
    groups = group_related_queries(
        [
            _query("customer", sql_creating("cust"), 1),
            _query("insurance", sql_creating("claims"), 2),
        ]
    )

    assert _names(groups) == [["customer"], ["insurance"]]


def test_a_subfolder_feeding_another_puts_both_in_one_group():
    groups = group_related_queries(
        [
            _query("base", sql_creating("cust_base"), 1),
            _query("features", sql_creating("cust_feat", reads=["cust_base"]), 2),
        ]
    )

    assert _names(groups) == [["base", "features"]]


def test_the_producer_comes_first_within_a_group():
    """Merge order: the query creating a table has to land before the one reading it."""
    groups = group_related_queries(
        [
            _query("features", sql_creating("cust_feat", reads=["cust_base"]), 1),
            _query("base", sql_creating("cust_base"), 2),
        ]
    )

    assert _names(groups) == [["base", "features"]]


def test_grouping_is_transitive():
    """a <- b <- c is one group, even though a and c reference nothing of each other's."""
    groups = group_related_queries(
        [
            _query("a", sql_creating("ta"), 1),
            _query("c", sql_creating("tc", reads=["tb"]), 2),
            _query("b", sql_creating("tb", reads=["ta"]), 3),
        ]
    )

    assert _names(groups) == [["a", "b", "c"]]


def test_related_and_unrelated_subfolders_in_one_upload():
    groups = group_related_queries(
        [
            _query("base", sql_creating("cust_base"), 1),
            _query("claims", sql_creating("claims"), 2),
            _query("lookup", sql_creating("lookup"), 3),
            _query("features", sql_creating("cust_feat", reads=["cust_base"]), 4),
            _query("claims_agg", sql_creating("claims_agg", reads=["claims"]), 5),
        ]
    )

    assert _names(groups) == [["base", "features"], ["claims", "claims_agg"], ["lookup"]]


def test_groups_come_in_order_of_their_first_member():
    """Stable output: the order subfolders were read in, not an arbitrary set order."""
    groups = group_related_queries(
        [
            _query("zeta", sql_creating("z"), 1),
            _query("alpha", sql_creating("a"), 2),
        ]
    )

    assert _names(groups) == [["zeta"], ["alpha"]]


def test_a_single_query_is_a_group_of_one():
    assert _names(group_related_queries([_query("only", sql_creating("t"), 1)])) == [["only"]]


def test_no_queries_is_no_groups():
    assert group_related_queries([]) == []


def test_a_cycle_stays_one_group():
    """Mutually dependent subfolders can't be ordered, but they're still one unit."""
    groups = group_related_queries(
        [
            _query("left", sql_creating("l", reads=["r"]), 1),
            _query("right", sql_creating("r", reads=["l"]), 2),
        ]
    )

    assert len(groups) == 1
    assert sorted(q["name"] for q in groups[0]) == ["left", "right"]


def test_every_query_lands_in_exactly_one_group():
    queries = [
        _query("base", sql_creating("cust_base"), 1),
        _query("features", sql_creating("cust_feat", reads=["cust_base"]), 2),
        _query("lookup", sql_creating("lookup"), 3),
    ]

    grouped = [q["name"] for group in group_related_queries(queries) for q in group]

    assert sorted(grouped) == ["base", "features", "lookup"]


# ---------------------------------------------------------------- conversions


@pytest.fixture
def uploaded(tmp_path, monkeypatch):
    """One upload whose subfolders form two independent groups plus a loner."""
    root = tmp_path / "source_sql_file"
    (root / "churn").mkdir(parents=True)
    (root / "churn" / "base.sql").write_text(sql_creating("cust_base"))
    (root / "churn" / "claims.sql").write_text(sql_creating("claims"))
    (root / "churn" / "features.sql").write_text(sql_creating("cust_feat", reads=["cust_base"]))
    (root / "churn" / "lookup.sql").write_text(sql_creating("lookup"))
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(root))
    return root


def test_a_conversion_reports_its_groups(uploaded):
    conversion = load_conversions()[0]

    assert [g["domains"] for g in conversion["groups"]] == [
        ["base", "features"],
        ["claims"],
        ["lookup"],
    ]


def test_group_query_ids_match_the_conversions_queries(uploaded):
    conversion = load_conversions()[0]

    from_groups = sorted(qid for g in conversion["groups"] for qid in g["query_ids"])

    assert from_groups == sorted(conversion["query_ids"])


def test_groups_carry_no_sql(uploaded):
    """This crosses the wire on every file listing - it stays ids and names."""
    conversion = load_conversions()[0]

    assert all(set(g) == {"domains", "query_ids"} for g in conversion["groups"])


def test_a_single_domain_conversion_has_one_group(tmp_path, monkeypatch):
    root = tmp_path / "source_sql_file"
    root.mkdir(parents=True)
    (root / "lone.sql").write_text(sql_creating("t"))
    monkeypatch.setattr(query_service, "SQL_DIRECTORY", str(root))

    assert load_conversions()[0]["groups"] == [{"domains": ["lone"], "query_ids": [1]}]
