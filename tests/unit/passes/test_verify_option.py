from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import PassState
from dbtw.core.passes.tier2 import append_pass, merge_pass
from dbtw.core.passes.types import merge_option, verify_option

APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders"
MERGE_ONE_KEY = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.customer_id = s.customer_id "
    "WHEN MATCHED THEN UPDATE SET t.name = s.name"
)
MERGE_TWO_KEYS = (
    "MERGE INTO fact_x AS t USING stg_x AS s "
    "ON t.order_id = s.order_id AND t.line_no = s.line_no "
    "WHEN MATCHED THEN UPDATE SET t.qty = s.qty"
)


def _question(pass_fn, text, kind):
    raw = RawStatement(source_file="t.sql", index=0, text=text, line_start=1, line_end=1)
    stmt = ClassifiedStatement(raw=raw, kind=kind, reason="test")
    out = pass_fn(PassState(pending=((0, stmt),), drafts=(), decisions=(), dialect=None))
    (dec,) = [d for d in out.decisions if d.question]
    return dec


def test_the_single_key_merge_question_offers_a_checked_answer():
    dec = _question(merge_pass, MERGE_ONE_KEY, "merge")
    labels = [o.label for o in dec.options]
    assert verify_option(("customer_id",)).label in labels
    assert dec.chosen == merge_option(("customer_id",)).label  # default stays the script's merge


def test_the_multi_key_merge_question_does_not_offer_a_check_it_cannot_honour():
    """dbt's built-in unique test checks one column. Declaring it per-column on
    a two-column key asserts each column is unique alone -- stronger than the
    key claim, and failing on valid data. Not offered is the honest answer."""
    dec = _question(merge_pass, MERGE_TWO_KEYS, "merge")
    assert not any("checked on every run" in o.label for o in dec.options)


def test_the_append_question_offers_the_keyless_checked_answer():
    dec = _question(append_pass, APPEND, "insert_select")
    keyless = verify_option()
    match = [o for o in dec.options if o.label == keyless.label]
    assert len(match) == 1
    assert match[0].columns_prompt, "the keyless verify must ask for its column"


def test_the_checked_answer_explains_the_check_in_both_registers():
    option = verify_option(("customer_id",))
    assert "unique" in option.effect  # dbt-native register names the test
    assert option.plain and option.plain != option.effect


def test_the_plain_wording_does_not_imply_the_merge_is_blocked():
    """The check is dbt's `unique` test, declared in a .yml and evaluated
    only when `dbt test`/`dbt build` is separately invoked -- after the
    merge has already written. The merge still quietly picks its row; the
    check reports the violation on its own next run. A plain wording saying
    the check "stops", "prevents", or "blocks" anything tells a newcomer the
    merge itself is halted before harm, which is not what happens."""
    for option in (verify_option(), verify_option(("customer_id",))):
        plain = option.plain.lower()
        for blocking_verb in ("stops", "prevents", "blocks"):
            assert blocking_verb not in plain, f"{option.label}: {option.plain}"
        # The defensible fact this wording has to keep, not just avoid
        # overstating: the check still fails loudly, later, and names the
        # offending value -- so a fix that drops the consequence entirely
        # (rather than just softening its timing) fails here too.
        assert "next time it runs" in plain, option.plain
        assert "names the value" in plain, option.plain
