from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import PassState
from dbtw.core.passes.tier1 import build_models_pass


def _stmt(text: str, kind: str, index: int = 0, file: str = "t.sql") -> ClassifiedStatement:
    raw = RawStatement(source_file=file, index=index, text=text, line_start=1, line_end=1)
    return ClassifiedStatement(raw=raw, kind=kind, reason="test")  # type: ignore[arg-type]


def _state(*items: tuple[int, ClassifiedStatement], dialect: str | None = None) -> PassState:
    return PassState(pending=tuple(items), drafts=(), decisions=(), dialect=dialect)


def test_ctas_becomes_table_draft_and_is_consumed():
    stmt = _stmt(
        "CREATE TABLE analytics.dim_customers AS SELECT id, name FROM raw_customers",
        "create_table_as",
    )
    out = build_models_pass(_state((0, stmt)))
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "dim_customers"
    assert draft.materialization == "table"
    assert "FROM raw_customers" in draft.body
    assert draft.source_indices == (0,)
    (dec,) = out.decisions
    assert dec.tier == 1
    assert "dim_customers" in dec.action


def test_view_becomes_view_draft():
    stmt = _stmt("CREATE OR REPLACE VIEW v_orders AS SELECT * FROM orders", "create_view")
    out = build_models_pass(_state((0, stmt)))
    assert out.drafts[0].materialization == "view"
    assert out.drafts[0].name == "v_orders"


def test_select_into_builds_table_draft_with_plain_select_body():
    stmt = _stmt("SELECT id, name INTO dim_customers FROM raw_customers", "create_table_as")
    out = build_models_pass(
        PassState(pending=((0, stmt),), drafts=(), decisions=(), dialect="tsql")
    )
    (draft,) = out.drafts
    assert draft.name == "dim_customers"
    assert "INTO" not in draft.body.upper()
    assert "FROM raw_customers" in draft.body


def test_statement_comments_are_captured():
    stmt = _stmt(
        "-- the customer dimension\nCREATE TABLE dim_c AS SELECT 1 AS a", "create_table_as"
    )
    out = build_models_pass(_state((0, stmt)))
    assert out.drafts[0].leading_comments == ("the customer dimension",)


def test_redefinition_keeps_last_and_records_decision():
    first = _stmt("CREATE TABLE d AS SELECT 1 AS a", "create_table_as", index=0)
    second = _stmt("CREATE TABLE d AS SELECT 2 AS a", "create_table_as", index=1)
    out = build_models_pass(_state((0, first), (1, second)))
    (draft,) = out.drafts
    assert "SELECT" in draft.body and "2" in draft.body
    assert any("redefinition" in d.action for d in out.decisions)


def test_non_build_kinds_stay_pending():
    stmt = _stmt("GRANT SELECT ON x TO r", "grant")
    out = build_models_pass(_state((0, stmt)))
    assert out.pending == ((0, stmt),)
    assert out.drafts == ()
