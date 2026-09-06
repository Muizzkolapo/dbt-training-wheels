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
    append_option = next(o for o in dec.options if o.label == "append every row")
    assert "duplicates" in append_option.effect
    merge_option = next(o for o in dec.options if o.label == "merge on a unique key")
    assert "uniquely" in merge_option.effect


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


def test_the_option_that_needs_columns_says_so_on_the_record():
    """A consumer rendering `dec.options` -- the report, or the web UI this
    surface exists for -- has to know which option needs a column input
    before it can offer one. Deciding that by comparing the label against
    "merge on a unique key" makes every consumer carry its own copy of that
    string, which is the duplication these options exist to remove. The
    requirement travels on the Option, and the prompt is the wording every
    consumer shows.
    """
    dec = _question(_run(append_pass, APPEND, "insert_select"))
    needing = [o for o in dec.options if o.columns_prompt]
    assert [o.label for o in needing] == ["merge on a unique key"]
    assert "uniquely" in needing[0].columns_prompt
    # The option that settles itself asks for nothing.
    (append_alternative,) = [o for o in dec.options if o.label == "append every row"]
    assert append_alternative.columns_prompt == ""


def test_a_merge_option_that_names_its_keys_needs_no_columns():
    """The key is in the label, so there is nothing left for a consumer to
    ask for."""
    dec = _question(_run(merge_pass, MERGE, "merge"))
    assert [o.columns_prompt for o in dec.options] == ["", ""]
