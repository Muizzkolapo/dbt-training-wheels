import pytest
from tests.unit.assemble.helpers import context_for, convert, state_with_variable

from dbtw.core.assemble import UnknownAnswerError, assemble
from dbtw.core.passes import Decision, PassState, append_option, merge_option
from dbtw.core.passes.types import Answer

TWO_APPENDS = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
    "INSERT INTO page_views SELECT order_id, ts FROM stg_views;\n"
)
ONE_APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
ONE_MERGE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n"
)


def _variable_decision(change):
    (dec,) = [d for d in change.decisions if d.question.startswith("Is ")]
    return dec


def _question_for(change, table):
    (dec,) = [d for d in change.decisions if d.question and table in d.action]
    return dec


def test_an_answer_overrides_the_inline_vars_default():
    """--inline-vars is the CLI's blanket answer to every variable question.
    A per-decision answer is the same question answered one variable at a
    time, and it wins where it is given."""
    state, ctx = state_with_variable(), context_for()
    baseline = assemble(state, ctx, inline_vars=False)
    key = _variable_decision(baseline).key

    answered = assemble(
        state, ctx, inline_vars=False, answers={key: Answer("inline the literal value")}
    )
    assert _variable_decision(answered).chosen == "inline the literal value"
    assert _variable_decision(baseline).chosen == "keep as a dbt var"


def test_an_answer_to_inline_changes_the_rendered_body_not_just_the_decision():
    """The Decision's `chosen` field is a claim about what the emitted model
    does. If the answer flips `chosen` to "inline the literal value" but the
    body still calls var('cutoff'), the claim is a lie the body doesn't back
    up, and the emitted project would carry an undeclared assumption about
    where its own value comes from. An answered run must match the
    equivalent flag-driven run byte for byte, not just agree on the label."""
    state, ctx = state_with_variable(), context_for()
    key = _variable_decision(assemble(state, ctx)).key

    answered = assemble(
        state, ctx, inline_vars=False, answers={key: Answer("inline the literal value")}
    )
    flag_driven = assemble(state, ctx, inline_vars=True)

    assert answered.models[0].body == flag_driven.models[0].body
    assert "'2024-01-01'" in answered.models[0].body
    assert "var('cutoff')" not in answered.models[0].body
    assert answered.variables == ()


def test_an_answer_to_keep_as_a_var_keeps_the_body_a_var_under_inline_vars():
    """The mirror of the above: an answer overriding a *global*
    --inline-vars=True back toward "keep as a dbt var" must leave the body
    calling var() and must keep the variable declared in change.variables --
    the direction nothing else exercises."""
    state, ctx = state_with_variable(), context_for()
    key = _variable_decision(assemble(state, ctx)).key

    answered = assemble(state, ctx, inline_vars=True, answers={key: Answer("keep as a dbt var")})

    assert "var('cutoff')" in answered.models[0].body
    assert "'2024-01-01'" not in answered.models[0].body
    assert [v.name for v in answered.variables] == ["cutoff"]


def test_an_answer_naming_an_option_that_was_not_offered_is_refused():
    """A label nobody offered cannot be honoured, and silently ignoring it
    would leave the screen showing an answer the run did not take."""
    state, ctx = state_with_variable(), context_for()
    key = _variable_decision(assemble(state, ctx)).key
    with pytest.raises(UnknownAnswerError):
        assemble(state, ctx, answers={key: Answer("delete the variable")})


def test_an_answer_for_an_unknown_decision_is_refused():
    """A stale key -- from an edited script, say -- must not pass silently."""
    state, ctx = state_with_variable(), context_for()
    with pytest.raises(UnknownAnswerError):
        assemble(state, ctx, answers={"assemble.variable.nope": Answer("keep as a dbt var")})


def test_an_answer_to_a_decision_this_run_cannot_apply_is_refused():
    """An inherited incremental question is answerable only when the statement
    it describes actually became a model in this change. Here it did not --
    the state carries the Decision with no draft behind it, the shape a
    deferred or superseded statement leaves -- so there is nothing for an
    answer to change, and answering it must be refused exactly like a stale
    key rather than silently accepted while the change stays as it was."""
    inherited = Decision(
        key="tier2.append.e.sql:0",
        tier=2,
        action="INSERT INTO orders became an append incremental model",
        reason="an INSERT...SELECT with no MERGE evidence defaults to append",
        source_file="e.sql",
        line_start=1,
        line_end=1,
        question="Should rows be appended on every run, or deduplicated on a unique key?",
        chosen=append_option().label,
        options=(append_option(), merge_option()),
    )
    state = PassState(pending=(), drafts=(), decisions=(inherited,), dialect=None)
    ctx = context_for()
    with pytest.raises(UnknownAnswerError):
        assemble(state, ctx, answers={inherited.key: Answer(merge_option().label)})


def test_a_merge_answer_upgrades_only_the_model_it_names():
    """--unique-key is a blanket over every eligible append model. A per-
    decision answer is the point of the web layer: two models in one script
    can take different keys, or one can stay an append."""
    revenue_q = _question_for(convert(TWO_APPENDS), "revenue_events")

    answered = convert(
        TWO_APPENDS, answers={revenue_q.key: Answer("merge on a unique key", ("order_id",))}
    )
    revenue = next(m for m in answered.models if "revenue_events" in m.name)
    views = next(m for m in answered.models if "page_views" in m.name)
    assert revenue.incremental_strategy == "merge"
    assert revenue.unique_key == ("order_id",)
    assert views.incremental_strategy == "append"
    assert views.unique_key == ()


def test_an_upgrade_from_an_answer_says_so_instead_of_blaming_the_flag():
    """The upgraded Decision is the report's account of why this model merges.
    An answer-driven upgrade that reads "--unique-key was supplied on the
    command line" describes a run that never happened."""
    revenue_q = _question_for(convert(ONE_APPEND), "revenue_events")

    answered = convert(
        ONE_APPEND, answers={revenue_q.key: Answer("merge on a unique key", ("order_id",))}
    )
    upgraded = next(d for d in answered.decisions if d.key == revenue_q.key)
    assert upgraded.chosen == merge_option(("order_id",)).label
    assert [o.label for o in upgraded.options] == ["merge on order_id", "append every row"]
    assert "--unique-key" not in upgraded.reason
    assert "--unique-key" not in upgraded.action
    assert "order_id" in upgraded.reason


def test_a_merge_answer_with_no_columns_is_refused():
    """ "merge on a unique key" names no key. Applying it with an empty tuple
    would write `unique_key=[]`, which dbt rejects at run time -- and the
    screen would have reported a merge that cannot run."""
    key = _question_for(convert(ONE_APPEND), "revenue_events").key
    # Matched on the message: this question IS answerable now, so a bare
    # `raises` here would also pass on a run that refused the key itself.
    with pytest.raises(UnknownAnswerError, match="with no columns"):
        convert(ONE_APPEND, answers={key: Answer("merge on a unique key")})


def test_columns_on_an_option_that_cannot_use_them_are_refused():
    """Only "merge on a unique key" leaves its key unsaid, so only it consumes
    `columns`. Accepting them anywhere else means discarding them in silence,
    which leaves the caller believing a key was recorded somewhere."""
    incremental = _question_for(convert(ONE_APPEND), "revenue_events").key
    with pytest.raises(UnknownAnswerError, match="takes no columns"):
        convert(ONE_APPEND, answers={incremental: Answer("append every row", ("order_id",))})

    state, ctx = state_with_variable(), context_for()
    variable = _variable_decision(assemble(state, ctx)).key
    with pytest.raises(UnknownAnswerError, match="takes no columns"):
        assemble(state, ctx, answers={variable: Answer("keep as a dbt var", ("cutoff",))})


def test_a_merge_answer_naming_a_column_the_model_does_not_select_is_declined():
    """The body check that guards --unique-key guards an answer too: forcing a
    key the model never projects would fail at dbt run time whichever input
    asked for it. Declined with a Decision, not applied and not silent."""
    key = _question_for(convert(ONE_APPEND), "revenue_events").key

    answered = convert(ONE_APPEND, answers={key: Answer("merge on a unique key", ("customer_id",))})
    (model,) = answered.models
    assert model.incremental_strategy == "append"
    assert model.unique_key == ()
    (declined,) = [
        d for d in answered.decisions if d.key == "assemble.unique_key_not_selected.revenue_events"
    ]
    assert "customer_id" in declined.action
    assert "--unique-key" not in declined.action


def test_answering_append_on_a_merge_downgrades_it():
    """The mirror: a MERGE's key came off its ON clause, and the user may
    disagree that it identifies a row uniquely."""
    key = _question_for(convert(ONE_MERGE), "dim_c").key
    answered = convert(ONE_MERGE, answers={key: Answer("append every row")})
    (model,) = answered.models
    assert model.incremental_strategy == "append"
    assert model.unique_key == ()
    downgraded = next(d for d in answered.decisions if d.key == key)
    assert downgraded.chosen == "append every row"
    assert "merge on id" in [o.label for o in downgraded.options]


def test_answering_a_merge_question_with_its_own_key_changes_nothing():
    """Confirming the default is a real answer, and it must leave the model
    exactly as the script's ON clause built it."""
    baseline = convert(ONE_MERGE)
    key = _question_for(baseline, "dim_c").key
    answered = convert(ONE_MERGE, answers={key: Answer("merge on id")})
    assert answered.models == baseline.models
    assert next(d for d in answered.decisions if d.key == key).chosen == "merge on id"


def test_an_answer_wins_over_the_blanket_flag_and_says_the_flag_was_dropped():
    """The two inputs can be given at once and can disagree. The narrower one
    wins per model, the wider one still applies everywhere else, and the
    report has to name the flag it did not apply -- otherwise a run told two
    different things looks like it was only ever told one."""
    views_q = _question_for(convert(TWO_APPENDS), "page_views")

    both = convert(
        TWO_APPENDS,
        unique_key=("order_id",),
        answers={views_q.key: Answer("append every row")},
    )
    revenue = next(m for m in both.models if "revenue_events" in m.name)
    views = next(m for m in both.models if "page_views" in m.name)
    assert (revenue.incremental_strategy, revenue.unique_key) == ("merge", ("order_id",))
    assert (views.incremental_strategy, views.unique_key) == ("append", ())
    (overridden,) = [
        d for d in both.decisions if d.key == "assemble.unique_key_overridden.page_views"
    ]
    assert "--unique-key order_id" in overridden.action
    assert "append every row" in overridden.action


def test_an_answer_and_the_equivalent_flag_produce_the_same_models():
    """One code path, two ways in. If these ever diverge, the browser and the
    command line have started converting the same script differently."""
    by_flag = convert(ONE_APPEND, unique_key=("order_id",))
    key = _question_for(convert(ONE_APPEND), "revenue_events").key
    by_answer = convert(ONE_APPEND, answers={key: Answer("merge on a unique key", ("order_id",))})
    assert [(m.name, m.incremental_strategy, m.unique_key) for m in by_flag.models] == [
        (m.name, m.incremental_strategy, m.unique_key) for m in by_answer.models
    ]
    # Not just the three incremental fields: the emitted file -- body, path,
    # materialization and all -- has to be the same file either way.
    assert by_answer.models == by_flag.models
