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
    Both come from the engine so the report and the screen cannot disagree.

    A third answer -- `verify_option()`, offering to have dbt check the key
    instead of asking the user to vouch for it -- now rides alongside these
    two; this test only asserts the original pair is still among them."""
    dec = _question(_run(append_pass, APPEND, "insert_select"))
    assert len(dec.options) == 3
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


# A claim about *how* dbt breaks is one this engine cannot make: it never
# runs dbt. Both registers of the keyless merge option used to say the run
# "fails"; dbt-core's merge macro appears instead to substitute a false join
# predicate and drop the matched branch, i.e. to append. Neither reading was
# checkable here, so neither ships.
UNSUPPORTABLE = ("fail", "error", "crash")


def _empty_key_consequence(text: str, anchor: str) -> str:
    """The clause about leaving the key empty, cut away from the sentence
    before it. That sentence describes what a merge WITH a key does, in the
    same two verbs, and would satisfy every assertion below on its own."""
    assert anchor in text, text
    return text.split(anchor, 1)[1].lower()


def test_the_keyless_merge_option_names_the_cost_of_leaving_the_key_empty():
    """`merge_option()` with no keys is the one option a reader can answer
    with nothing to key on -- the refusal for that used to carry this
    consequence only as a code comment at the validation site, invisible to
    anyone reading the report or the screen. It belongs on the option itself.

    What it may say has narrowed twice now: first to drop the claim that the
    run fails, which this engine cannot establish, and then to drop "every
    row is added" too -- that is the appended-branch reading, and if dbt
    errors instead, no row is added. What's left is the half that holds
    whichever way dbt behaves -- with no key there is nothing to match on,
    so nothing gets updated. Both registers carry it, because they sit one
    under the other in the report and must not disagree.
    """
    dec = _question(_run(append_pass, APPEND, "insert_select"))
    merge_option = next(o for o in dec.options if o.label == "merge on a unique key")

    effect = _empty_key_consequence(merge_option.effect, "empty unique_key")
    assert "nothing to match" in effect, merge_option.effect
    assert "updated" in effect, merge_option.effect
    assert "added" not in effect, merge_option.effect

    plain = _empty_key_consequence(merge_option.plain, "without")
    assert "nothing to compare" in plain, merge_option.plain
    assert "updated" in plain, merge_option.plain
    assert "added" not in plain, merge_option.plain

    for claim in UNSUPPORTABLE:
        assert claim not in merge_option.effect.lower(), merge_option.effect
        assert claim not in merge_option.plain.lower(), merge_option.plain

    # The keyed variant can't have an empty key by construction -- its
    # caller always supplies one read off the SQL -- so the same warning
    # would describe a cost this option can never carry.
    keyed_dec = _question(_run(merge_pass, MERGE, "merge"))
    keyed_option = next(o for o in keyed_dec.options if o.label == "merge on id")
    assert "empty unique_key" not in keyed_option.effect
    assert "nothing to match" not in keyed_option.effect.lower()


def test_the_chosen_label_is_always_one_of_the_offered_options():
    """A `chosen` naming an option that was never offered would render as an
    answer nobody could have given."""
    for out in (
        _run(append_pass, APPEND, "insert_select"),
        _run(merge_pass, MERGE, "merge"),
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
    # Both keyless answers need a column: the plain merge and its checked
    # sibling (`verify_option()`) ask the same "which column" question.
    assert [o.label for o in needing] == [
        "merge on a unique key",
        "merge on a unique key, checked on every run",
    ]
    assert "uniquely" in needing[0].columns_prompt
    # The option that settles itself asks for nothing.
    (append_alternative,) = [o for o in dec.options if o.label == "append every row"]
    assert append_alternative.columns_prompt == ""


def test_a_merge_option_that_names_its_keys_needs_no_columns():
    """The key is in the label, so there is nothing left for a consumer to
    ask for -- true of the merge answer, the append alternative, and (MERGE's
    key here is a single column) the checked variant `verify_option` offers
    alongside it."""
    dec = _question(_run(merge_pass, MERGE, "merge"))
    assert [o.columns_prompt for o in dec.options] == ["", "", ""]


def test_no_question_recommends_a_command_line_flag():
    """A question, its reason and its options are rendered together to a
    reader who may have no command line -- the web layer this surface exists
    for -- and the options already offer the same switch in-band. Prose
    telling that reader to "supply --unique-key" points at a door they
    cannot open, directly above the button that does the same thing.
    """
    for out in (_run(append_pass, APPEND, "insert_select"), _run(merge_pass, MERGE, "merge")):
        dec = _question(out)
        rendered = " ".join(
            [dec.action, dec.reason, dec.question, *(o.label + " " + o.effect for o in dec.options)]
        )
        assert "--" not in rendered, rendered


def test_the_append_question_describes_the_merge_alternative_not_the_flag():
    """What the reader is choosing between, in the same terms the options
    use: a key that identifies a row uniquely, not a flag name."""
    dec = _question(_run(append_pass, APPEND, "insert_select"))
    assert "identifies a row uniquely" in dec.reason
    assert "merge incremental" in dec.reason
