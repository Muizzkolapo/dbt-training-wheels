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
