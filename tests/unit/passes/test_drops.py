from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import PassState
from dbtw.core.passes.tier1 import drop_ddl_pass, drop_session_pass


def _stmt(text: str, kind: str, index: int = 0) -> ClassifiedStatement:
    raw = RawStatement(source_file="t.sql", index=index, text=text, line_start=1, line_end=1)
    return ClassifiedStatement(raw=raw, kind=kind, reason="test")  # type: ignore[arg-type]


def _state(*items: tuple[int, ClassifiedStatement]) -> PassState:
    return PassState(pending=tuple(items), drafts=(), decisions=(), dialect=None)


def test_session_statements_dropped_with_reason():
    out = drop_session_pass(_state((0, _stmt("USE analytics", "session"))))
    assert out.pending == ()
    (dec,) = out.decisions
    assert dec.tier == 1
    assert "profiles.yml" in dec.reason


def test_ddl_other_dropped_naming_post_hook():
    out = drop_ddl_pass(_state((0, _stmt("CREATE INDEX ix ON x (a)", "ddl_other"))))
    assert out.pending == ()
    (dec,) = out.decisions
    assert "post-hook" in dec.reason


def test_solo_truncate_dropped_by_ddl_pass():
    out = drop_ddl_pass(_state((0, _stmt("TRUNCATE TABLE t", "truncate"))))
    assert out.pending == ()
    (dec,) = out.decisions
    assert "rebuilds from scratch" in dec.reason


def test_later_tier_kinds_untouched_by_both():
    var = _stmt("DECLARE @d INT = 1", "variable", 0)
    proc = _stmt("EXEC p", "procedural", 1)
    for p in (drop_session_pass, drop_ddl_pass):
        out = p(_state((0, var), (1, proc)))
        assert out.pending == ((0, var), (1, proc))
        assert out.decisions == ()
