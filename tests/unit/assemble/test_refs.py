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


def test_quoted_case_distinct_table_read_is_not_swallowed_by_a_differently_cased_cte():
    """FINDING 9 correction: unconditionally casefolding the CTE-alias
    comparison (round 1's fix) introduced a false positive — a genuinely
    case-sensitive QUOTED table read now vanished as if it were the CTE.
    Postgres quoted identifiers are case-sensitive: "totals" and "Totals"
    are different names. Casefolding must apply only when NEITHER
    identifier is quoted; when either is, the comparison must be exact.
    """
    body = 'WITH "Totals" AS (SELECT 1 AS x) SELECT * FROM "totals"'
    assert references_in(body, "postgres") == (TableRef(catalog="", db="", name="totals"),)


def test_one_side_quoted_forces_an_exact_comparison_too():
    """Mixed quoting (one side quoted, the other not) is the conservative
    case: an exact comparison is required, not a casefolded one — an
    unquoted CTE and a quoted, case-distinct read must not silently match.
    """
    body = 'WITH Totals AS (SELECT 1 AS x) SELECT * FROM "totals"'
    assert references_in(body, "postgres") == (TableRef(catalog="", db="", name="totals"),)


def test_quoted_same_case_table_read_still_matches_its_cte():
    """A quoted read that matches its CTE's alias exactly (same case) must
    still be recognized as the CTE, not treated as an external reference.
    """
    body = 'WITH "Totals" AS (SELECT 1 AS x) SELECT * FROM "Totals"'
    assert references_in(body, "postgres") == ()


def test_a_cte_in_a_subquery_does_not_shadow_a_read_outside_it():
    """A CTE's name reaches only the query its WITH is attached to.

    Every caller of the old `is_cte_read` collected CTEs with an unscoped
    `find_all(exp.CTE)`, so a CTE anywhere in the statement subtracted a read
    of its name anywhere else. Here the real `orders` table read at the top
    level vanished from a body's references entirely: never declared as a
    source, never a dependency, and never rewritten -- the body kept a bare
    `FROM orders` that dbt would send to the warehouse as written.
    """
    body = (
        "WITH tidy AS (SELECT id FROM raw.src) "
        "SELECT id FROM orders "
        "UNION ALL "
        "SELECT id FROM (WITH orders AS (SELECT 1 AS id) SELECT id FROM orders) AS sub "
        "UNION ALL "
        "SELECT id FROM tidy"
    )

    # `orders` because the top-level read is a real table; `src` because the
    # outer CTE reads one. `tidy` and the inner `orders` are CTE reads.
    #
    # The unrelated CTE `tidy` is here on purpose. `references_in` returns a
    # deduplicated set, so a body whose only CTE name is also read for real
    # collapses to the same one-element answer whether CTE reads are subtracted
    # or not — which let an earlier version of this test pass against an
    # implementation with no CTE-awareness at all. `tidy` is read and is never
    # a table, so it appears if and only if the subtraction stopped happening.
    assert references_in(body, None) == (
        TableRef(catalog="", db="", name="orders"),
        TableRef(catalog="", db="raw", name="src"),
    )


def test_a_ctes_own_body_reads_the_real_table_of_its_name():
    """`WITH orders AS (SELECT id FROM orders)` is the ordinary way to wrap a
    real table, and the inner read is that table: a non-recursive CTE is not
    in scope inside its own definition. Treating it as the CTE dropped the
    only reference the body actually had, leaving a model that reads nothing.
    """
    body = (
        "WITH tidy AS (SELECT 1 AS id), orders AS (SELECT id FROM orders) "
        "SELECT id FROM orders UNION ALL SELECT id FROM tidy"
    )

    # `tidy` is read and is never a table, so it appears only if CTE reads
    # stopped being subtracted at all — see the note on the test above.
    assert [r.name for r in references_in(body, None)] == ["orders"]


def test_a_recursive_ctes_own_body_reads_itself_not_a_table():
    """The opposite case, and why the rule is not simply "a CTE cannot see
    itself": RECURSIVE is precisely the declaration that it can. Reading this
    one as an external table would announce an undeclared source for a name
    that is defined three words earlier.
    """
    body = (
        "WITH RECURSIVE walk AS ("
        "SELECT 1 AS n UNION ALL SELECT n + 1 AS n FROM walk WHERE n < 10"
        ") SELECT n FROM walk"
    )

    assert references_in(body, None) == ()


def test_a_cte_defined_after_another_is_not_in_scope_inside_it():
    """Order within one WITH is load-bearing: `first` cannot read `second`,
    so a read of `second` inside `first` is a real table, and calling it the
    CTE would swallow a reference the model genuinely depends on.
    """
    body = "WITH first AS (SELECT id FROM second), second AS (SELECT 1 AS id) SELECT id FROM first"

    assert [r.name for r in references_in(body, None)] == ["second"]
