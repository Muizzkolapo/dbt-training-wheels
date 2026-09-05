import dataclasses

from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import ModelDraft, PassState
from dbtw.core.passes.tier1 import grants_pass


def _stmt(text: str, index: int = 0) -> ClassifiedStatement:
    raw = RawStatement(source_file="t.sql", index=index, text=text, line_start=1, line_end=1)
    return ClassifiedStatement(raw=raw, kind="grant", reason="test")


def _draft(name: str) -> ModelDraft:
    return ModelDraft(
        name=name,
        qualified_name=name,
        identity=("", "", name.casefold()),
        body="SELECT 1 AS a",
        materialization="table",
        grants=(),
        source_indices=(0,),
        leading_comments=(),
    )


def test_grant_attaches_to_matching_draft():
    state = PassState(
        pending=((1, _stmt("GRANT SELECT, INSERT ON dim_c TO reporting, ops")),),
        drafts=(_draft("dim_c"),),
        decisions=(),
        dialect=None,
    )
    out = grants_pass(state)
    assert out.pending == ()
    (draft,) = out.drafts
    assert ("SELECT", ("reporting", "ops")) in draft.grants
    assert ("INSERT", ("reporting", "ops")) in draft.grants
    (dec,) = out.decisions
    assert "dim_c" in dec.action


def test_unmatched_grant_dropped_with_note():
    state = PassState(
        pending=((0, _stmt("GRANT SELECT ON somewhere_else TO r")),),
        drafts=(_draft("dim_c"),),
        decisions=(),
        dialect=None,
    )
    out = grants_pass(state)
    assert out.pending == ()
    assert out.drafts[0].grants == ()
    (dec,) = out.decisions
    assert "doesn't create" in dec.action


def test_revoke_dropped_with_note():
    state = PassState(
        pending=((0, _stmt("REVOKE SELECT ON dim_c FROM r")),),
        drafts=(_draft("dim_c"),),
        decisions=(),
        dialect=None,
    )
    out = grants_pass(state)
    assert out.pending == ()
    assert out.drafts[0].grants == ()
    (dec,) = out.decisions
    assert "REVOKE" in dec.action


def test_grants_pass_changes_only_the_grants_field():
    """Locks the general property behind the incremental-fields regression:
    grants_pass must change ONLY .grants on the draft it matches. Every
    field is set to a non-default, distinguishable value up front and
    checked generically via dataclasses.fields, so a future ModelDraft field
    addition doesn't need this test rewritten to stay meaningful -- it was
    exactly a hand-enumerated field list (one that predated
    incremental_strategy/unique_key) that let grants_pass silently drop
    them when a GRANT attached to an incremental model."""
    draft = ModelDraft(
        name="dim_c",
        qualified_name="db.schema.dim_c",
        identity=("db", "schema", "dim_c"),
        body="SELECT 1 AS id",
        materialization="incremental",
        grants=(("UPDATE", ("someone",)),),
        source_indices=(3, 7),
        leading_comments=("a leading comment",),
        incremental_strategy="merge",
        unique_key=("id", "region"),
    )
    state = PassState(
        pending=((0, _stmt("GRANT SELECT ON dim_c TO reporting")),),
        drafts=(draft,),
        decisions=(),
        dialect=None,
    )
    out = grants_pass(state)
    (result,) = out.drafts
    for field in dataclasses.fields(ModelDraft):
        if field.name == "grants":
            continue
        assert getattr(result, field.name) == getattr(draft, field.name), (
            f"grants_pass changed {field.name!r}, which a GRANT should never touch"
        )
    assert result.grants != draft.grants
    assert ("SELECT", ("reporting",)) in result.grants


def test_non_grant_kinds_untouched():
    raw = RawStatement(source_file="t.sql", index=0, text="SELECT 1", line_start=1, line_end=1)
    sel = ClassifiedStatement(raw=raw, kind="select", reason="test")
    state = PassState(pending=((0, sel),), drafts=(), decisions=(), dialect=None)
    out = grants_pass(state)
    assert out.pending == ((0, sel),)
