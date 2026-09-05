from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import PassState
from dbtw.core.passes.tier2 import append_pass


def _stmt(text: str, kind: str, index: int, file: str = "t.sql") -> ClassifiedStatement:
    raw = RawStatement(source_file=file, index=index, text=text, line_start=1, line_end=1)
    return ClassifiedStatement(raw=raw, kind=kind, reason="test")  # type: ignore[arg-type]


def _state(*items: tuple[int, ClassifiedStatement]) -> PassState:
    return PassState(pending=tuple(items), drafts=(), decisions=(), dialect=None)


def test_bare_insert_select_becomes_an_append_incremental():
    out = append_pass(
        _state((0, _stmt("INSERT INTO events SELECT a FROM raw_e", "insert_select", 0)))
    )
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "events"
    assert draft.materialization == "incremental"
    assert draft.incremental_strategy == "append"
    assert draft.unique_key == ()
    assert "raw_e" in draft.body


def test_the_decision_offers_the_unique_key_alternative():
    out = append_pass(
        _state((0, _stmt("INSERT INTO events SELECT a FROM raw_e", "insert_select", 0)))
    )
    (dec,) = out.decisions
    assert dec.tier == 2
    assert dec.question
    assert dec.chosen == "append every row"
    assert any("unique key" in a for a in dec.alternatives)
    assert "re-inserts" in dec.reason


def test_an_existing_where_is_named_as_evidence_not_turned_into_a_guard():
    stmt = _stmt(
        "INSERT INTO events SELECT a FROM raw_e WHERE d > '2024-01-01'", "insert_select", 0
    )
    out = append_pass(_state((0, stmt)))
    draft = out.drafts[0]
    assert "is_incremental" not in draft.body
    assert any("d > '2024-01-01'" in d.reason for d in out.decisions)


def test_a_column_list_insert_is_left_alone():
    out = append_pass(_state((0, _stmt("INSERT INTO t (a) SELECT x FROM s", "insert_select", 0))))
    assert out.drafts == ()
    assert len(out.pending) == 1


def test_a_delete_on_the_same_target_defers_the_pair():
    dele = _stmt("DELETE FROM events WHERE d >= '2024-01-01'", "delete", 0)
    ins = _stmt("INSERT INTO events SELECT a FROM raw_e", "insert_select", 1)
    out = append_pass(_state((0, dele), (1, ins)))
    assert out.drafts == ()
    assert len(out.pending) == 2
    assert any("delete and insert" in d.action for d in out.decisions)


def test_case_differing_delete_target_still_defers():
    dele = _stmt("DELETE FROM Events WHERE d >= '2024-01-01'", "delete", 0)
    ins = _stmt("INSERT INTO events SELECT a FROM raw_e", "insert_select", 1)
    out = append_pass(_state((0, dele), (1, ins)))
    assert out.drafts == ()
    assert any("delete and insert" in d.action for d in out.decisions)


def test_differently_qualified_delete_target_defers_as_ambiguous():
    dele = _stmt("DELETE FROM db.events WHERE d >= '2024-01-01'", "delete", 0)
    ins = _stmt("INSERT INTO events SELECT a FROM raw_e", "insert_select", 1)
    out = append_pass(_state((0, dele), (1, ins)))
    assert out.drafts == ()
    assert any("delete and insert" in d.action for d in out.decisions)


def test_a_delete_on_a_different_target_does_not_defer():
    dele = _stmt("DELETE FROM other_table WHERE d >= '2024-01-01'", "delete", 0)
    ins = _stmt("INSERT INTO events SELECT a FROM raw_e", "insert_select", 1)
    out = append_pass(_state((0, dele), (1, ins)))
    assert len(out.drafts) == 1
    assert out.drafts[0].incremental_strategy == "append"


def test_a_delete_in_a_different_file_defers_just_the_same():
    """This used to assert the opposite -- that a DELETE in another file was
    ignored and the INSERT converted to an append anyway.

    The file scoping was borrowed from `truncate_insert_pass`, which PAIRS two
    statements into one model and so is making a claim about adjacency within
    one script. This check only DECLINES to convert, and the reason it gives
    for declining -- that an append re-inserts everything on every run
    regardless of the DELETE, silently changing which rows survive -- is true
    whichever file the DELETE was written in. One conversion produces one dbt
    project, and a DELETE against this target is part of it either way."""
    dele = _stmt("DELETE FROM events WHERE d >= '2024-01-01'", "delete", 0, file="a.sql")
    ins = _stmt("INSERT INTO events SELECT a FROM raw_e", "insert_select", 1, file="b.sql")
    out = append_pass(_state((0, dele), (1, ins)))
    assert out.drafts == ()
    assert any("delete and insert" in d.action for d in out.decisions)
