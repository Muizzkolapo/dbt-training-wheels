from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import PassState
from dbtw.core.passes.tier2 import append_pass, merge_pass

APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders"
MERGE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.customer_id = s.customer_id "
    "WHEN MATCHED THEN UPDATE SET t.name = s.name"
)


def _question(pass_fn, text, kind):
    raw = RawStatement(source_file="t.sql", index=0, text=text, line_start=1, line_end=1)
    stmt = ClassifiedStatement(raw=raw, kind=kind, reason="test")
    out = pass_fn(PassState(pending=((0, stmt),), drafts=(), decisions=(), dialect=None))
    (dec,) = [d for d in out.decisions if d.question]
    return dec


def test_the_merge_question_names_the_table_and_the_key_it_asks_about():
    """The question text says 'customer_id' and 'dim_c' in prose. A consumer
    that needs to offer a check query, or name the column in a form field,
    must not have to parse that sentence to recover them."""
    dec = _question(merge_pass, MERGE, "merge")
    assert dec.subject is not None
    assert dec.subject.table == "dim_c"
    assert dec.subject.columns == ("customer_id",)


def test_the_append_question_names_its_table_and_offers_no_key_yet():
    """An append question has no candidate key -- that is what it is asking.
    An empty columns tuple is the honest answer, not a guess."""
    dec = _question(append_pass, APPEND, "insert_select")
    assert dec.subject is not None
    assert dec.subject.table == "revenue_events"
    assert dec.subject.columns == ()


def test_the_subject_table_keeps_the_spelling_the_script_used():
    """Identity comparison casefolds; display must not. A user looking for
    'Revenue_Events' in their own script should find it spelled that way."""
    dec = _question(
        append_pass,
        "INSERT INTO Revenue_Events SELECT order_id FROM stg_orders",
        "insert_select",
    )
    assert dec.subject is not None
    assert dec.subject.table == "Revenue_Events"


def test_every_question_in_a_real_conversion_carries_a_subject():
    """The rule this task establishes, asserted against a whole conversion
    rather than one pass -- so a question added later without a subject
    fails here rather than reaching a consumer that cannot render it."""
    from dbtw.core.assemble import assemble
    from dbtw.core.context import read_project
    from dbtw.core.ingest import classify_statements, ingest
    from dbtw.core.passes import run_passes

    ir = ingest("tests/fixtures/sql/incremental_etl.sql", None)
    change = assemble(
        run_passes(classify_statements(ir), ir.dialect),
        read_project("tests/fixtures/projects/jaffle_shop"),
    )
    questions = [d for d in change.decisions if d.question]
    assert questions, "fixture stopped producing questions; pick another"
    for d in questions:
        assert d.subject is not None, f"{d.key} asks a question with no subject"
        assert d.subject.table, f"{d.key} has a subject with no table"
