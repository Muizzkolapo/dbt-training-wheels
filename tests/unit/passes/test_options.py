from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import PassState
from dbtw.core.passes.tier2 import append_pass, merge_pass

APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders"
MERGE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *"
)


def _run(pass_fn, text, kind):
    raw = RawStatement(source_file="t.sql", index=0, text=text, line_start=1, line_end=1)
    stmt = ClassifiedStatement(raw=raw, kind=kind, reason="test")
    return pass_fn(PassState(pending=((0, stmt),), drafts=(), decisions=(), dialect=None))


def _question(out):
    (dec,) = [d for d in out.decisions if d.question]
    return dec


def test_an_append_question_describes_both_of_its_answers():
    """The label is the button; the effect is what dbt does if you press it.
    Both come from the engine so the report and the screen cannot disagree."""
    dec = _question(_run(append_pass, APPEND, "insert_select"))
    assert len(dec.options) == 2
    labels = [o.label for o in dec.options]
    assert "append every row" in labels
    assert "merge on a unique key" in labels
    assert dec.chosen in labels
    for option in dec.options:
        assert option.effect, f"{option.label} explains nothing"
        assert option.effect != option.label


def test_a_merge_question_names_its_key_in_the_option_it_offers():
    """The merge option is specific to the key read off the ON clause, not
    generic — the button says what it will actually key on."""
    dec = _question(_run(merge_pass, MERGE, "merge"))
    labels = [o.label for o in dec.options]
    assert "merge on id" in labels
    assert "append every row" in labels
    merge_option = next(o for o in dec.options if o.label == "merge on id")
    assert "id" in merge_option.effect


def test_the_chosen_label_is_always_one_of_the_offered_options():
    """A `chosen` naming an option that was never offered would render as an
    answer nobody could have given."""
    for out, _text, _kind in (
        (_run(append_pass, APPEND, "insert_select"), APPEND, "insert_select"),
        (_run(merge_pass, MERGE, "merge"), MERGE, "merge"),
    ):
        dec = _question(out)
        assert dec.chosen in [o.label for o in dec.options]
