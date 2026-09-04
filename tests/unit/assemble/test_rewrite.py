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
