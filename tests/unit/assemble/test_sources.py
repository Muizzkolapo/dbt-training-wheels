from pathlib import Path

from dbtw.core.assemble import SourceEntry, assemble
from dbtw.core.context import read_project
from dbtw.core.passes import ModelDraft, PassState

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _state(body: str) -> PassState:
    draft = ModelDraft(
        name="m",
        qualified_name="m",
        body=body,
        materialization="table",
        grants=(),
        source_indices=(0,),
        leading_comments=(),
    )
    return PassState(pending=(), drafts=(draft,), decisions=(), dialect=None)


def test_qualified_external_reference_becomes_a_source_entry():
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(_state("SELECT a FROM raw.payments"), ctx)
    assert change.sources == (SourceEntry(source_name="raw", schema="raw", table="payments"),)


def test_source_already_declared_in_the_target_is_skipped():
    ctx = read_project(FIXTURES / "with_sources")  # declares raw.customers, raw.orders
    change = assemble(_state("SELECT a FROM raw.customers JOIN raw.payments ON 1 = 1"), ctx)
    assert [s.table for s in change.sources] == ["payments"]
    skipped = [d for d in change.decisions if "already declared" in d.action]
    assert len(skipped) == 1
    assert "sources.yml" in skipped[0].reason


def test_unqualified_reference_is_reported_not_invented():
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(_state("SELECT a FROM raw_orders"), ctx)
    assert change.sources == ()
    assert any("not schema-qualified" in d.action for d in change.decisions)


def test_references_to_models_in_this_change_are_not_sources():
    ctx = read_project(FIXTURES / "jaffle_shop")
    drafts = (
        ModelDraft(
            name="a_m",
            qualified_name="a_m",
            body="SELECT x FROM raw.t",
            materialization="table",
            grants=(),
            source_indices=(0,),
            leading_comments=(),
        ),
        ModelDraft(
            name="b_m",
            qualified_name="b_m",
            body="SELECT x FROM a_m",
            materialization="table",
            grants=(),
            source_indices=(1,),
            leading_comments=(),
        ),
    )
    change = assemble(PassState(pending=(), drafts=drafts, decisions=(), dialect=None), ctx)
    assert [s.table for s in change.sources] == ["t"]


def test_references_to_existing_target_models_are_not_sources():
    ctx = read_project(FIXTURES / "jaffle_shop")  # has stg_orders, customers, orders
    change = assemble(_state("SELECT a FROM stg_orders"), ctx)
    assert change.sources == ()


def test_qualified_reference_is_not_suppressed_by_a_bare_name_matching_existing_model():
    """jaffle_shop already has a model named `orders` (models/orders.sql). A
    schema-qualified read of raw.orders is a different table entirely and
    must still surface as a source — an unqualified existing-model name can
    never rule out a qualified reference.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(_state("SELECT a FROM raw.orders"), ctx)
    assert change.sources == (SourceEntry(source_name="raw", schema="raw", table="orders"),)


def test_qualified_reference_already_declared_still_yields_the_dedup_decision():
    """with_sources declares raw.orders as a source. A qualified read of
    raw.orders must still be recognized as the already-declared source (and
    skipped with a Decision explaining why), not silently dropped.
    """
    ctx = read_project(FIXTURES / "with_sources")  # declares raw.customers, raw.orders
    change = assemble(_state("SELECT a FROM raw.orders"), ctx)
    assert change.sources == ()
    skipped = [d for d in change.decisions if "already declared" in d.action]
    assert len(skipped) == 1
    assert "raw.orders" in skipped[0].action


def test_qualified_reference_dedup_decision_survives_a_coexisting_bare_named_draft():
    """A draft literally named `orders` sitting alongside a draft reading
    raw.orders must not suppress the already-declared source Decision — the
    two are unrelated: one is this change's own model, the other is an
    external, schema-qualified reference to a declared source.
    """
    ctx = read_project(FIXTURES / "with_sources")  # declares raw.customers, raw.orders
    drafts = (
        ModelDraft(
            name="orders",
            qualified_name="analytics.orders",
            body="SELECT 1 AS x",
            materialization="table",
            grants=(),
            source_indices=(0,),
            leading_comments=(),
        ),
        ModelDraft(
            name="m",
            qualified_name="m",
            body="SELECT a FROM raw.orders",
            materialization="table",
            grants=(),
            source_indices=(1,),
            leading_comments=(),
        ),
    )
    change = assemble(PassState(pending=(), drafts=drafts, decisions=(), dialect=None), ctx)
    assert change.sources == ()
    skipped = [d for d in change.decisions if "already declared" in d.action]
    assert len(skipped) == 1


def test_two_catalogs_of_the_same_table_do_not_collapse_into_one_source():
    """FINDING 2 probe: FROM prod.raw.orders AS a JOIN dev.raw.orders AS b —
    two different catalogs' tables that happen to share (schema, table).
    Before the fix, both `resolve_references` (matching sources by (db,
    name)) and `_source_entries` (bucketing external refs the same way)
    discarded `ref.catalog`, so the two joined tables collapsed onto the
    SAME `{{ source('raw', 'orders') }}` call — a self-join of one table —
    with a single proposed source entry and zero Decisions recorded. Neither
    catalog-qualified ref has a safe (db, name)-only source declaration
    (sources.yml carries no catalog), so both must be left unresolved and as
    written, and no source entry may be proposed for either.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(
        _state("SELECT a FROM prod.raw.orders AS a JOIN dev.raw.orders AS b ON 1 = 1"), ctx
    )
    assert change.sources == ()
    unresolved = [d for d in change.decisions if "left as written" in d.action]
    assert len(unresolved) == 2
    assert all("database" in d.reason.lower() for d in unresolved)
    body = change.models[0].body
    assert "{{ source(" not in body
    assert "{{ ref(" not in body
    assert "prod" in body and "raw" in body and "dev" in body
