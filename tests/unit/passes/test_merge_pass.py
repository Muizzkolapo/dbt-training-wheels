from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import PassState
from dbtw.core.passes.tier2 import merge_pass

MERGE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.n = s.n"
)
MERGE_TWO_KEYS = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.a = s.a AND t.b = s.b "
    "WHEN MATCHED THEN UPDATE SET t.n = s.n"
)
MERGE_NO_KEY = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON 1 = 1 WHEN MATCHED THEN UPDATE SET t.n = s.n"
)


def _stmt(text: str, index: int = 0) -> ClassifiedStatement:
    raw = RawStatement(source_file="t.sql", index=index, text=text, line_start=1, line_end=1)
    return ClassifiedStatement(raw=raw, kind="merge", reason="test")


def _state(*items: tuple[int, ClassifiedStatement]) -> PassState:
    return PassState(pending=tuple(items), drafts=(), decisions=(), dialect=None)


def test_merge_becomes_an_incremental_model_keyed_on_the_on_clause():
    out = merge_pass(_state((0, _stmt(MERGE))))
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "dim_c"
    assert draft.materialization == "incremental"
    assert draft.incremental_strategy == "merge"
    assert draft.unique_key == ("id",)
    assert "stg_c" in draft.body


def test_composite_key_keeps_both_columns_in_order():
    out = merge_pass(_state((0, _stmt(MERGE_TWO_KEYS))))
    assert out.drafts[0].unique_key == ("a", "b")


def test_the_decision_asks_whether_the_key_is_unique():
    out = merge_pass(_state((0, _stmt(MERGE))))
    (dec,) = out.decisions
    assert dec.tier == 2
    assert dec.question
    assert "id" in dec.chosen
    assert dec.alternatives


def test_merge_without_an_extractable_key_stays_pending():
    out = merge_pass(_state((0, _stmt(MERGE_NO_KEY))))
    assert len(out.pending) == 1
    assert out.drafts == ()
    assert any("no unique key" in d.action for d in out.decisions)


def test_non_merge_kinds_are_untouched():
    raw = RawStatement(source_file="t.sql", index=0, text="SELECT 1", line_start=1, line_end=1)
    sel = ClassifiedStatement(raw=raw, kind="select", reason="t")
    out = merge_pass(PassState(pending=((0, sel),), drafts=(), decisions=(), dialect=None))
    assert out.pending == ((0, sel),)


def test_or_joined_on_clause_is_refused_not_treated_as_a_composite_key():
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s "
        "ON t.id = s.id OR t.legacy_id = s.legacy_id WHEN MATCHED THEN UPDATE SET t.n = s.n"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert len(out.pending) == 1
    assert any("no unique key" in d.action or "disjunct" in d.reason.lower() for d in out.decisions)


def test_source_first_on_clause_still_keys_on_the_target_column():
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON s.src_id = t.id "
        "WHEN MATCHED THEN UPDATE SET t.n = s.n"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts[0].unique_key == ("id",)


def test_unaliased_target_matches_on_its_name():
    sql = (
        "MERGE INTO dim_c USING stg_c AS s ON dim_c.id = s.id WHEN MATCHED THEN UPDATE SET n = s.n"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts[0].unique_key == ("id",)


def test_equality_qualified_to_neither_side_is_excluded():
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON other.x = elsewhere.y "
        "WHEN MATCHED THEN UPDATE SET t.n = s.n"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert any("no unique key" in d.action for d in out.decisions)
