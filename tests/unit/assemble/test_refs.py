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


def test_qualified_reference_survives_even_when_its_bare_name_matches_a_cte():
    body = "WITH orders AS (SELECT * FROM raw.orders) SELECT * FROM orders"
    assert references_in(body, None) == (TableRef("", "raw", "orders"),)


def test_qualified_reference_is_never_mistaken_for_a_cte_even_by_join():
    body = "WITH c AS (SELECT 1 AS x) SELECT * FROM c JOIN raw.c ON 1 = 1"
    refs = references_in(body, None)
    assert refs == (TableRef("", "raw", "c"),)


def test_cte_alias_matching_is_case_insensitive():
    """FINDING 9 probe (tsql, case-insensitive identifiers): `WITH Totals AS
    (...) SELECT * FROM totals` reads the CTE, not some external `totals`
    table — cte_names compared the alias's original case against the read's
    original case and missed the match, so the CTE read looked exactly like
    an undeclared external reference.
    """
    body = "WITH Totals AS (SELECT 1 AS x) SELECT * FROM totals"
    assert references_in(body, "tsql") == ()
