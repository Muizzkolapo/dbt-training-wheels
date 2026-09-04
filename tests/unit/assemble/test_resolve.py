from dbtw.core.assemble import TableRef
from dbtw.core.assemble.resolve import resolve_references


def _resolve(ref, **kw):
    return resolve_references(
        (ref,),
        draft_to_final=kw.get("draft_to_final", {}),
        qualified_to_final=kw.get("qualified_to_final", {}),
        existing_models=kw.get("existing_models", frozenset()),
        declared_sources=kw.get("declared_sources", {}),
        proposed_sources=kw.get("proposed_sources", {}),
    )[0]


def test_unqualified_ref_to_a_model_in_this_change():
    r = _resolve(
        TableRef("", "", "order_totals"), draft_to_final={"order_totals": "stg_order_totals"}
    )
    assert (r.kind, r.target) == ("ref", "stg_order_totals")


def test_unqualified_ref_to_an_existing_target_model():
    r = _resolve(TableRef("", "", "customers"), existing_models=frozenset({"customers"}))
    assert (r.kind, r.target) == ("ref", "customers")
    assert "target project" in r.reason


def test_qualified_ref_to_a_declared_source():
    r = _resolve(TableRef("", "raw", "orders"), declared_sources={("raw", "orders"): "raw"})
    assert (r.kind, r.source_name, r.target) == ("source", "raw", "orders")


def test_qualified_ref_to_a_source_we_are_proposing():
    r = _resolve(TableRef("", "raw", "payments"), proposed_sources={("raw", "payments"): "raw"})
    assert r.kind == "source"


def test_qualified_ref_matches_a_draft_only_by_qualified_name():
    ref = TableRef("", "analytics", "orders")
    r = _resolve(ref, qualified_to_final={"analytics.orders": "stg_orders"})
    assert (r.kind, r.target) == ("ref", "stg_orders")


def test_qualified_ref_never_matches_an_unqualified_existing_model():
    """The slice-4 Critical: raw.orders must not resolve to the target's bare `orders` model."""
    r = _resolve(TableRef("", "raw", "orders"), existing_models=frozenset({"orders"}))
    assert r.kind == "unresolved"


def test_qualified_ref_never_matches_a_bare_draft_name():
    r = _resolve(TableRef("", "raw", "orders"), draft_to_final={"orders": "stg_orders"})
    assert r.kind == "unresolved"


def test_unqualified_external_ref_is_unresolved_with_a_reason():
    r = _resolve(TableRef("", "", "raw_orders"))
    assert r.kind == "unresolved"
    assert "schema" in r.reason.lower()


def test_a_model_in_this_change_wins_over_a_same_named_source():
    r = _resolve(
        TableRef("", "raw", "orders"),
        qualified_to_final={"raw.orders": "stg_orders"},
        declared_sources={("raw", "orders"): "raw"},
    )
    assert (r.kind, r.target) == ("ref", "stg_orders")


def test_resolution_count_matches_input_count():
    refs = (TableRef("", "", "a"), TableRef("", "raw", "b"), TableRef("", "", "c"))
    assert len(resolve_references(refs, {}, {}, frozenset(), {}, {})) == 3


def test_fully_qualified_ref_uses_the_whole_three_part_key():
    r = _resolve(
        TableRef("prod", "analytics", "dim_c"),
        qualified_to_final={"prod.analytics.dim_c": "stg_dim_c"},
    )
    assert (r.kind, r.target) == ("ref", "stg_dim_c")


def test_a_different_catalog_is_not_the_same_table():
    """Probed regression: prod.analytics.dim_c must not resolve to another catalog's model."""
    r = _resolve(
        TableRef("prod", "analytics", "dim_c"),
        qualified_to_final={"analytics.dim_c": "stg_UNRELATED"},
    )
    assert r.kind == "unresolved"


def test_catalog_only_ref_is_qualified_and_never_bare_matches():
    r = _resolve(
        TableRef("mydb", "", "orders"),
        draft_to_final={"orders": "stg_orders"},
        existing_models=frozenset({"orders"}),
    )
    assert r.kind == "unresolved"


def test_catalog_only_ref_says_a_schema_is_needed():
    r = _resolve(TableRef("mydb", "", "orders"))
    assert r.kind == "unresolved"
    assert "schema" in r.reason.lower()
