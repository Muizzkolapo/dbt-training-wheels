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


def test_non_grant_kinds_untouched():
    raw = RawStatement(source_file="t.sql", index=0, text="SELECT 1", line_start=1, line_end=1)
    sel = ClassifiedStatement(raw=raw, kind="select", reason="test")
    state = PassState(pending=((0, sel),), drafts=(), decisions=(), dialect=None)
    out = grants_pass(state)
    assert out.pending == ((0, sel),)
