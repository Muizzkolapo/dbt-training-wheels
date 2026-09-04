from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import PassState
from dbtw.core.passes.tier1 import truncate_insert_pass


def _stmt(text: str, kind: str, index: int, file: str = "t.sql") -> ClassifiedStatement:
    raw = RawStatement(source_file=file, index=index, text=text, line_start=1, line_end=1)
    return ClassifiedStatement(raw=raw, kind=kind, reason="test")  # type: ignore[arg-type]


def _state(*items: tuple[int, ClassifiedStatement]) -> PassState:
    return PassState(pending=tuple(items), drafts=(), decisions=(), dialect=None)


def test_truncate_then_insert_becomes_one_table_model():
    tr = _stmt("TRUNCATE TABLE stg_daily", "truncate", 0)
    ins = _stmt("INSERT INTO stg_daily SELECT a, b FROM raw_t", "insert_select", 1)
    out = truncate_insert_pass(_state((0, tr), (1, ins)))
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "stg_daily"
    assert draft.materialization == "table"
    assert "FROM raw_t" in draft.body
    assert draft.source_indices == (0, 1)
    (dec,) = out.decisions
    assert dec.tier == 1
    assert "full rebuild" in dec.reason


def test_insert_before_truncate_does_not_pair():
    ins = _stmt("INSERT INTO stg_daily SELECT a FROM raw_t", "insert_select", 0)
    tr = _stmt("TRUNCATE TABLE stg_daily", "truncate", 1)
    out = truncate_insert_pass(_state((0, ins), (1, tr)))
    assert len(out.pending) == 2
    assert out.drafts == ()


def test_cross_schema_truncate_and_insert_do_not_pair():
    # TRUNCATE staging.t and INSERT INTO mart.t are different tables that
    # merely share an unqualified name; pairing on name alone would silently
    # fold two unrelated tables into one model.
    tr = _stmt("TRUNCATE TABLE staging.t", "truncate", 0)
    ins = _stmt("INSERT INTO mart.t SELECT a FROM raw_t", "insert_select", 1)
    out = truncate_insert_pass(_state((0, tr), (1, ins)))
    assert len(out.pending) == 2
    assert out.drafts == ()
    assert out.decisions == ()


def test_different_files_do_not_pair():
    tr = _stmt("TRUNCATE TABLE stg_daily", "truncate", 0, file="a.sql")
    ins = _stmt("INSERT INTO stg_daily SELECT a FROM raw_t", "insert_select", 1, file="b.sql")
    out = truncate_insert_pass(_state((0, tr), (1, ins)))
    assert len(out.pending) == 2


def test_insert_with_column_list_defers_to_tier2():
    tr = _stmt("TRUNCATE TABLE stg_daily", "truncate", 0)
    ins = _stmt("INSERT INTO stg_daily (a, b) SELECT x, y FROM raw_t", "insert_select", 1)
    out = truncate_insert_pass(_state((0, tr), (1, ins)))
    assert len(out.pending) == 2
    assert out.drafts == ()
    (dec,) = out.decisions
    assert dec.tier == 2
    assert "column" in dec.reason.lower()


def test_solo_truncate_stays_pending():
    tr = _stmt("TRUNCATE TABLE stg_daily", "truncate", 0)
    out = truncate_insert_pass(_state((0, tr)))
    assert out.pending == ((0, tr),)
    assert out.decisions == ()


def test_second_insert_after_pairing_stays_pending():
    tr = _stmt("TRUNCATE TABLE t", "truncate", 0)
    first = _stmt("INSERT INTO t SELECT a FROM src_a", "insert_select", 1)
    second = _stmt("INSERT INTO t SELECT a FROM src_b", "insert_select", 2)
    out = truncate_insert_pass(_state((0, tr), (1, first), (2, second)))
    (draft,) = out.drafts
    assert "src_a" in draft.body  # the first insert's SQL is NOT discarded
    assert draft.source_indices == (0, 1)
    assert [i for i, _ in out.pending] == [2]  # second insert awaits Tier 2


def test_insert_after_column_list_deferral_does_not_pair():
    tr = _stmt("TRUNCATE TABLE t", "truncate", 0)
    deferred = _stmt("INSERT INTO t (a) SELECT a FROM src_a", "insert_select", 1)
    later = _stmt("INSERT INTO t SELECT a FROM src_b", "insert_select", 2)
    out = truncate_insert_pass(_state((0, tr), (1, deferred), (2, later)))
    assert out.drafts == ()  # nothing pairs once the truncate is spoken for
    assert len(out.pending) == 3
