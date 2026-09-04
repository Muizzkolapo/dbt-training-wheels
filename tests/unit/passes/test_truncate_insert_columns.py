from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import PassState
from dbtw.core.passes.tier2 import truncate_insert_columns_pass


def _stmt(text: str, kind: str, index: int, file: str = "t.sql") -> ClassifiedStatement:
    raw = RawStatement(source_file=file, index=index, text=text, line_start=1, line_end=1)
    return ClassifiedStatement(raw=raw, kind=kind, reason="test")  # type: ignore[arg-type]


def _state(*items: tuple[int, ClassifiedStatement]) -> PassState:
    return PassState(pending=tuple(items), drafts=(), decisions=(), dialect=None)


def test_column_list_pair_becomes_one_table_model_with_aliases():
    tr = _stmt("TRUNCATE TABLE stg_daily", "truncate", 0)
    ins = _stmt("INSERT INTO stg_daily (a, b) SELECT x, y FROM raw_t", "insert_select", 1)
    out = truncate_insert_columns_pass(_state((0, tr), (1, ins)))
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "stg_daily"
    assert draft.materialization == "table"
    assert "x AS a" in draft.body
    assert "y AS b" in draft.body
    assert draft.source_indices == (0, 1)
    (dec,) = out.decisions
    assert dec.tier == 1
    assert "stg_daily" in dec.action


def test_insert_without_a_column_list_is_left_for_tier_one():
    tr = _stmt("TRUNCATE TABLE t", "truncate", 0)
    ins = _stmt("INSERT INTO t SELECT x FROM s", "insert_select", 1)
    out = truncate_insert_columns_pass(_state((0, tr), (1, ins)))
    assert len(out.pending) == 2
    assert out.drafts == ()


def test_mismatched_column_count_does_not_pair():
    tr = _stmt("TRUNCATE TABLE t", "truncate", 0)
    ins = _stmt("INSERT INTO t (a, b) SELECT x FROM s", "insert_select", 1)
    out = truncate_insert_columns_pass(_state((0, tr), (1, ins)))
    assert out.drafts == ()
    assert len(out.pending) == 2
    assert any("column count" in d.action for d in out.decisions)


def test_star_projection_cannot_be_mapped():
    tr = _stmt("TRUNCATE TABLE t", "truncate", 0)
    ins = _stmt("INSERT INTO t (a, b) SELECT * FROM s", "insert_select", 1)
    out = truncate_insert_columns_pass(_state((0, tr), (1, ins)))
    assert out.drafts == ()
    assert any("cannot map" in d.action for d in out.decisions)


def test_cross_file_does_not_pair():
    tr = _stmt("TRUNCATE TABLE t", "truncate", 0, file="a.sql")
    ins = _stmt("INSERT INTO t (a) SELECT x FROM s", "insert_select", 1, file="b.sql")
    out = truncate_insert_columns_pass(_state((0, tr), (1, ins)))
    assert out.drafts == ()
    assert len(out.pending) == 2


def test_insert_before_truncate_does_not_pair():
    ins = _stmt("INSERT INTO t (a) SELECT x FROM s", "insert_select", 0)
    tr = _stmt("TRUNCATE TABLE t", "truncate", 1)
    out = truncate_insert_columns_pass(_state((0, ins), (1, tr)))
    assert out.drafts == ()
    assert len(out.pending) == 2


def test_qualified_star_projection_cannot_be_mapped():
    tr = _stmt("TRUNCATE TABLE t", "truncate", 0)
    ins = _stmt("INSERT INTO t (a) SELECT s.* FROM raw_t s", "insert_select", 1)
    out = truncate_insert_columns_pass(_state((0, tr), (1, ins)))
    assert out.drafts == ()
    assert len(out.pending) == 2
    assert any("cannot map" in d.action for d in out.decisions)


def test_count_star_is_not_treated_as_a_star_projection():
    tr = _stmt("TRUNCATE TABLE t", "truncate", 0)
    ins = _stmt("INSERT INTO t (n) SELECT COUNT(*) FROM raw_t", "insert_select", 1)
    out = truncate_insert_columns_pass(_state((0, tr), (1, ins)))
    (draft,) = out.drafts
    assert "AS n" in draft.body
