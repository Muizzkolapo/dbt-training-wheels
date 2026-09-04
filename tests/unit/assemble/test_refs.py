from dbtw.core.assemble import TableRef
from dbtw.core.assemble.refs import references_in


def test_plain_table():
    assert references_in("SELECT a FROM raw_orders", None) == (
        TableRef(catalog="", db="", name="raw_orders"),
    )


def test_cte_names_are_not_references():
    body = "WITH c AS (SELECT a FROM raw_orders) SELECT * FROM c JOIN other_t ON 1 = 1"
    names = [r.name for r in references_in(body, None)]
    assert "c" not in names
    assert set(names) == {"raw_orders", "other_t"}


def test_nested_ctes_are_all_excluded():
    body = "WITH a AS (SELECT 1 AS x), b AS (SELECT * FROM a) SELECT * FROM b"
    assert references_in(body, None) == ()


def test_qualification_is_preserved():
    refs = references_in("SELECT * FROM prod.analytics.dim_c", None)
    assert refs == (TableRef(catalog="prod", db="analytics", name="dim_c"),)


def test_aliases_do_not_leak_into_names():
    refs = references_in("SELECT * FROM raw_orders AS o JOIN raw_items i ON 1 = 1", None)
    assert {r.name for r in refs} == {"raw_orders", "raw_items"}


def test_derived_table_alias_is_not_a_reference():
    refs = references_in("SELECT * FROM (SELECT a FROM raw_orders) AS d", None)
    assert refs == (TableRef(catalog="", db="", name="raw_orders"),)


def test_duplicates_collapse_and_order_is_deterministic():
    body = "SELECT * FROM b_t JOIN a_t ON 1 = 1 JOIN b_t x ON 1 = 1"
    assert [r.name for r in references_in(body, None)] == ["a_t", "b_t"]


def test_unparseable_body_yields_no_references():
    assert references_in("SELEC nope FRM", None) == ()
