from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import PassState
from dbtw.core.passes.tier1 import build_models_pass, truncate_insert_pass


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


def test_cross_schema_ctas_is_an_honest_collision_not_a_redefinition():
    # staging.t and mart.t are different tables that share an unqualified
    # name; the pass must say "collision" (name clash across two distinct
    # source tables), not "redefinition" (one table defined twice).
    first = _stmt("CREATE TABLE staging.t AS SELECT 1 AS a", "create_table_as", index=0)
    second = _stmt("CREATE TABLE mart.t AS SELECT 2 AS a", "create_table_as", index=1)
    out = build_models_pass(_state((0, first), (1, second)))
    (draft,) = out.drafts
    assert draft.qualified_name == "mart.t"
    collisions = [d for d in out.decisions if "collision" in d.action]
    assert len(collisions) == 1
    assert "staging.t" in collisions[0].action
    assert "mart.t" in collisions[0].action
    assert not any("redefinition" in d.action for d in out.decisions)


def test_ctas_truncate_insert_file_order_keeps_the_later_definition():
    # File order CTAS(0) -> TRUNCATE(1) -> INSERT(2) on the same table t.
    # truncate_insert_pass pairs (1, 2) into a draft before build_models_pass
    # ever sees the CTAS at index 0. The CTAS must NOT clobber the later
    # full-rebuild draft, and the statement it came from must still be
    # consumed (with an honest "superseded" decision), not silently dropped
    # or left pending.
    ctas = _stmt("CREATE TABLE t AS SELECT 99 AS a", "create_table_as", index=0)
    tr = _stmt("TRUNCATE TABLE t", "truncate", index=1)
    ins = _stmt("INSERT INTO t SELECT a FROM raw_t", "insert_select", index=2)
    state0 = PassState(
        pending=((0, ctas), (1, tr), (2, ins)), drafts=(), decisions=(), dialect=None
    )

    paired = truncate_insert_pass(state0)
    out = build_models_pass(paired)

    assert out.pending == ()  # all three indices consumed
    (draft,) = out.drafts
    assert "raw_t" in draft.body  # the file-order-later INSERT body wins
    assert "99" not in draft.body  # the earlier CTAS body does not survive

    assert len(out.decisions) == 2
    supersede = [d for d in out.decisions if "superseded" in d.action]
    assert len(supersede) == 1
    assert "t.sql:0" in supersede[0].key  # references the superseded CTAS statement
    pairing = [d for d in out.decisions if d is not supersede[0]]
    assert len(pairing) == 1
    assert "t.sql:2" in pairing[0].key  # references the INSERT that completed the pair

    # Finding 5: no two decisions share a key.
    assert len({d.key for d in out.decisions}) == len(out.decisions)


def test_non_build_kinds_stay_pending():
    stmt = _stmt("GRANT SELECT ON x TO r", "grant")
    out = build_models_pass(_state((0, stmt)))
    assert out.pending == ((0, stmt),)
    assert out.drafts == ()
