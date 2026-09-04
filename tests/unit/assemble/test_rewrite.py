from dbtw.core.assemble.resolve import Resolution
from dbtw.core.assemble.rewrite import rewrite_body
from dbtw.core.assemble.types import TableRef


def _res(catalog, db, name, kind, target, source_name=""):
    return {
        (catalog, db, name): Resolution(
            ref=TableRef(catalog, db, name),
            kind=kind,
            target=target,
            source_name=source_name,
            reason="test",
        )
    }


def test_unqualified_table_becomes_a_ref():
    out = rewrite_body(
        "SELECT a FROM order_totals",
        None,
        _res("", "", "order_totals", "ref", "stg_order_totals"),
        {},
        False,
    )
    assert "{{ ref('stg_order_totals') }}" in out
    assert "order_totals" not in out.replace("stg_order_totals", "")


def test_qualified_table_becomes_a_source():
    out = rewrite_body(
        "SELECT a FROM raw.orders",
        None,
        _res("", "raw", "orders", "source", "orders", "raw"),
        {},
        False,
    )
    assert "{{ source('raw', 'orders') }}" in out


def test_table_alias_survives_the_rewrite():
    """Probed: a bare Var drops `AS o` and dangles every o.col reference."""
    out = rewrite_body(
        "SELECT o.id FROM raw.orders AS o",
        None,
        _res("", "raw", "orders", "source", "orders", "raw"),
        {},
        False,
    )
    assert "AS o" in out
    assert "o.id" in out


def test_unresolved_table_is_left_exactly_as_written():
    out = rewrite_body(
        "SELECT a FROM raw_orders",
        None,
        _res("", "", "raw_orders", "unresolved", "", ""),
        {},
        False,
    )
    assert "raw_orders" in out
    assert "{{" not in out


def test_cte_alias_is_never_rewritten():
    body = "WITH order_totals AS (SELECT 1 AS a) SELECT * FROM order_totals"
    out = rewrite_body(body, None, _res("", "", "order_totals", "ref", "stg_x"), {}, False)
    assert "{{ ref(" not in out


def test_cte_alias_is_never_rewritten_case_insensitively():
    """FINDING 9 probe: `WITH Totals AS (...) SELECT * FROM totals` (tsql,
    case-insensitive identifiers) plus a draft named `totals` rewrote the
    CTE read into {{ ref('stg_totals') }} — refs.py and rewrite.py must stay
    mirror images of each other, or one module's CTE-exclusion and the
    other's rewrite-exclusion disagree and the read silently becomes a
    different table.
    """
    body = "WITH Totals AS (SELECT 1 AS x) SELECT * FROM totals"
    out = rewrite_body(body, "tsql", _res("", "", "totals", "ref", "stg_totals"), {}, False)
    assert "{{ ref(" not in out
    assert "totals" in out.lower()


def test_quoted_case_distinct_table_is_rewritten_as_an_external_reference():
    """FINDING 9 correction (rewrite.py mirror of refs.py's fix): a quoted,
    case-distinct table read is a genuinely different, external table — not
    the differently-cased CTE — so it must still be a rewrite candidate.
    Unconditional casefolding (round 1) wrongly excluded it from rewriting,
    same as it wrongly excluded it from refs.py's output.
    """
    body = 'WITH "Totals" AS (SELECT 1 AS x) SELECT * FROM "totals"'
    out = rewrite_body(body, "postgres", _res("", "", "totals", "ref", "stg_totals"), {}, False)
    assert "{{ ref('stg_totals') }}" in out


def test_quoted_same_case_table_is_still_excluded_as_a_cte_read():
    body = 'WITH "Totals" AS (SELECT 1 AS x) SELECT * FROM "Totals"'
    out = rewrite_body(body, "postgres", _res("", "", "totals", "ref", "stg_totals"), {}, False)
    assert "{{ ref(" not in out


def test_parameter_becomes_a_var():
    out = rewrite_body(
        "SELECT a FROM t WHERE d >= @start_date", "tsql", {}, {"start_date": "'2024-01-01'"}, False
    )
    assert "{{ var('start_date') }}" in out


def test_parameter_is_inlined_when_asked():
    out = rewrite_body(
        "SELECT a FROM t WHERE d >= @start_date", "tsql", {}, {"start_date": "'2024-01-01'"}, True
    )
    assert "'2024-01-01'" in out
    assert "var(" not in out
    # An atomic literal default needs no defensive parens (FINDING 7 only
    # requires wrapping compound expressions).
    assert "('2024-01-01')" not in out


def _squashed(sql: str) -> str:
    """Whitespace-insensitive comparison: rewrite_body pretty-prints, and
    sqlglot's pretty printer puts a `Paren` node on its own indented lines,
    so a literal "(1 + 2) * 3" substring check would fail on formatting
    alone, not on the parenthesization this test actually cares about.
    """
    return sql.replace(" ", "").replace("\n", "")


def test_inline_vars_wraps_a_compound_default_in_parens():
    """FINDING 7 probe: DECLARE @n INT = 1 + 2, used as SELECT @n * 3. The
    parsed default (Add(1, 2)) replaced the Parameter node in-place with no
    grouping, so the generator emitted `1 + 2 * 3` — which evaluates to 7
    under normal operator precedence, not the 9 the original script computed
    with @n substituted as a value. The default must be parenthesized unless
    it's already atomic, so it always evaluates as a single unit wherever it
    lands.
    """
    out = rewrite_body("SELECT @n * 3 AS result", "tsql", {}, {"n": "1 + 2"}, True)
    assert "(1+2)*3" in _squashed(out)
    assert "1+2*3" not in _squashed(out)


def test_inline_vars_does_not_double_wrap_an_already_parenthesized_default():
    out = rewrite_body("SELECT @n * 3 AS result", "tsql", {}, {"n": "(1 + 2)"}, True)
    assert "(1+2)*3" in _squashed(out)
    assert "((1+2))*3" not in _squashed(out)


def test_unknown_parameter_is_left_alone():
    out = rewrite_body(
        "SELECT a FROM t WHERE d >= @other", "tsql", {}, {"start_date": "'x'"}, False
    )
    assert "@other" in out


def test_tables_and_parameters_rewrite_together():
    out = rewrite_body(
        "SELECT a FROM raw.orders AS o WHERE o.d >= @start_date",
        "tsql",
        _res("", "raw", "orders", "source", "orders", "raw"),
        {"start_date": "'2024-01-01'"},
        False,
    )
    assert "{{ source('raw', 'orders') }} AS o" in out
    assert "{{ var('start_date') }}" in out


def test_unparseable_body_is_returned_unchanged():
    assert rewrite_body("SELEC nope FRM", None, {}, {}, False) == "SELEC nope FRM"


def test_inline_vars_falls_back_to_a_var_call_when_the_default_will_not_parse():
    out = rewrite_body(
        "SELECT a FROM t WHERE d >= @start_date",
        "tsql",
        {},
        {"start_date": "SELEC garbage NOPE ("},
        True,
    )
    assert "{{ var('start_date') }}" in out


def test_getvariable_call_becomes_a_var():
    """FINDING 3: duckdb's SET VARIABLE / spark's SET VAR are read back via a
    GETVARIABLE('name') call, not a Parameter node — extract_variables()
    already consumes the declaring statement (Task 4), but rewrite_body only
    ever matched exp.Parameter, so the reference form was left untouched:
    the report claimed the variable was "referenced via var('cutoff')" while
    the body still called GETVARIABLE('cutoff'), which returns NULL at run
    time and filters every row.
    """
    out = rewrite_body(
        "SELECT a FROM t WHERE d >= GETVARIABLE('cutoff')",
        "duckdb",
        {},
        {"cutoff": "'2024-06-30'"},
        False,
    )
    assert "{{ var('cutoff') }}" in out
    assert "GETVARIABLE" not in out.upper()


def test_getvariable_call_is_case_insensitively_matched():
    out = rewrite_body(
        "SELECT a FROM t WHERE d >= getVariable('cutoff')",
        "duckdb",
        {},
        {"cutoff": "'2024-06-30'"},
        False,
    )
    assert "{{ var('cutoff') }}" in out


def test_getvariable_call_is_inlined_when_asked():
    out = rewrite_body(
        "SELECT a FROM t WHERE d >= GETVARIABLE('cutoff')",
        "duckdb",
        {},
        {"cutoff": "'2024-06-30'"},
        True,
    )
    assert "'2024-06-30'" in out
    assert "var(" not in out
    assert "GETVARIABLE" not in out.upper()


def test_unknown_getvariable_call_is_left_alone():
    out = rewrite_body(
        "SELECT a FROM t WHERE d >= GETVARIABLE('other')",
        "duckdb",
        {},
        {"cutoff": "'x'"},
        False,
    )
    assert "GETVARIABLE('other')" in out
    assert "var(" not in out


def test_spark_style_bare_identifier_reference_is_never_rewritten():
    """FINDING 3 correction: this test previously claimed
    GETVARIABLE('cutoff') "works under spark dialect too" — true only of
    sqlglot's parse (spark has no real GETVARIABLE either, so it falls back
    to the same generic Anonymous-function shape duckdb does), but false as
    a claim about real Spark/Databricks scripts, which have no GETVARIABLE
    function at all. A real Spark SET VAR-declared variable is read back by
    BARE IDENTIFIER (`SELECT cutoff`), which parses as exp.Column —
    indistinguishable from an ordinary column of the same name. rewrite_body
    must never treat a bare column as a variable reference just because its
    name happens to match one; only exp.Parameter (@name) and the
    unambiguous GETVARIABLE('name') call are ever rewritten — anything else
    would silently turn a real column into a var() call. (variables.py's
    spark deferral means extract_variables never even offers a
    spark-declared name into this function's `variables` map in practice —
    this is the belt-and-suspenders guarantee at the rewrite layer itself.)
    """
    out = rewrite_body(
        "SELECT cutoff FROM t WHERE d >= cutoff",
        "spark",
        {},
        {"cutoff": "'2024-06-30'"},
        False,
    )
    assert "var(" not in out
    assert "cutoff" in out


def test_getvariable_call_with_more_than_one_argument_is_left_alone():
    """A guard against over-matching: GETVARIABLE is only ever a clean,
    unambiguous rewrite target when it has exactly the one string-literal
    argument the real function takes.
    """
    out = rewrite_body(
        "SELECT a FROM t WHERE d >= GETVARIABLE('cutoff', 'extra')",
        "duckdb",
        {},
        {"cutoff": "'2024-06-30'"},
        False,
    )
    assert "var(" not in out
