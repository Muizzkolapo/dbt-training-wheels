import pytest
from tests.unit.assemble.helpers import context_for, convert, state_with_variable

from dbtw.core.assemble import UnknownAnswerError, assemble
from dbtw.core.assemble import assembler as assembler_module
from dbtw.core.emit.render import render_model
from dbtw.core.passes import Decision, Option, PassState, append_option, merge_option
from dbtw.core.passes import tier2 as tier2_module
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
# The one script that asks both kinds of answerable question: a script
# variable and an incremental strategy.
VARIABLE_AND_APPEND = (
    "DECLARE @cutoff DATE = '2024-01-01';\n"
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders "
    "WHERE order_date >= @cutoff;\n"
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


def test_the_inline_answer_is_recognised_by_the_label_the_question_offered(monkeypatch):
    """The gate accepts any label the question offers; the assembler then has
    to recognise that same label to apply it. A second, hand-spelled copy of
    the label lets the two drift: rename the offered option alone and the
    answer is still accepted, still reported back -- as "keep as a dbt var",
    the option nobody chose -- and the var quietly stays a var. Renaming the
    factory renames both at once, so this stays green only while the offer
    and the match come from it.
    """
    renamed = Option(label="splice the literal in", effect="the literal goes into the body")
    monkeypatch.setattr(assembler_module, "inline_option", lambda: renamed, raising=False)
    state, ctx = state_with_variable(), context_for()
    offered = _variable_decision(assemble(state, ctx)).options
    assert [o.label for o in offered] == ["splice the literal in", "keep as a dbt var"]

    answered = assemble(
        state,
        ctx,
        answers={_variable_decision(assemble(state, ctx)).key: Answer("splice the literal in")},
    )
    assert "'2024-01-01'" in answered.models[0].body
    assert "var('cutoff')" not in answered.models[0].body
    assert _variable_decision(answered).chosen == "splice the literal in"
    assert answered.variables == ()


def test_an_answer_naming_an_option_that_was_not_offered_is_refused():
    """A label nobody offered cannot be honoured, and silently ignoring it
    would leave the screen showing an answer the run did not take."""
    state, ctx = state_with_variable(), context_for()
    key = _variable_decision(assemble(state, ctx)).key
    # Matched on the message so a regression that stopped registering variable
    # questions as answerable at all cannot keep this green: it would still
    # raise here, just from the unknown-key check instead.
    with pytest.raises(UnknownAnswerError, match="does not offer"):
        assemble(state, ctx, answers={key: Answer("delete the variable")})


def test_an_answer_for_an_unknown_decision_is_refused():
    """A stale key -- from an edited script, say -- must not pass silently."""
    state, ctx = state_with_variable(), context_for()
    with pytest.raises(UnknownAnswerError, match="no question with key"):
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
    # Matched on the message, and load-bearing: this input -- a keyless merge
    # with no columns -- has a second, independent refusal waiting behind the
    # unknown-key one. A bare `raises` would go green on the very regression
    # this test names, an implementation that registered the Decision as
    # answerable off its question/options shape rather than off a model
    # actually carrying it.
    with pytest.raises(UnknownAnswerError, match="no question with key"):
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


def test_two_models_in_one_script_take_two_different_answers():
    """Answering one model differently from another is the whole point of a
    per-question answer -- a blanket --unique-key cannot do it. Both answers
    have to land, each on its own model. Every other test here sends exactly
    one answer, so an implementation that kept only the first or only the
    last of them, or applied one model's key to both, would pass all of them.
    """
    baseline = convert(TWO_APPENDS)
    revenue_q = _question_for(baseline, "revenue_events")
    views_q = _question_for(baseline, "page_views")

    answered = convert(
        TWO_APPENDS,
        answers={
            revenue_q.key: Answer("merge on a unique key", ("order_id",)),
            views_q.key: Answer("merge on a unique key", ("ts",)),
        },
    )
    revenue = next(m for m in answered.models if "revenue_events" in m.name)
    views = next(m for m in answered.models if "page_views" in m.name)
    assert (revenue.incremental_strategy, revenue.unique_key) == ("merge", ("order_id",))
    assert (views.incremental_strategy, views.unique_key) == ("merge", ("ts",))
    # The files that actually get written, not just the fields behind them:
    # each model carries its own key and not the other's.
    revenue_file, views_file = render_model(revenue), render_model(views)
    assert "unique_key='order_id'" in revenue_file
    assert "unique_key='ts'" not in revenue_file
    assert "unique_key='ts'" in views_file
    assert "unique_key='order_id'" not in views_file


def test_a_variable_answer_and_an_incremental_answer_both_apply_in_one_call():
    """The two kinds of question a caller can answer are registered at
    different points in assemble() -- the incremental ones before the models
    are placed, the variable ones during the rewrite -- and a screen showing
    both sends both back together. Neither kind may clear or shadow the
    other: one answered model that is also one answered variable has to come
    out merged on the key AND with the literal spliced in.
    """
    baseline = convert(VARIABLE_AND_APPEND, dialect="tsql")
    incremental_q = _question_for(baseline, "revenue_events")
    (variable_q,) = [d for d in baseline.decisions if d.key == "assemble.variable.cutoff"]

    answered = convert(
        VARIABLE_AND_APPEND,
        dialect="tsql",
        answers={
            incremental_q.key: Answer("merge on a unique key", ("order_id",)),
            variable_q.key: Answer("inline the literal value"),
        },
    )
    (model,) = answered.models
    rendered = render_model(model)
    assert (model.incremental_strategy, model.unique_key) == ("merge", ("order_id",))
    assert "unique_key='order_id'" in rendered
    assert "'2024-01-01'" in rendered
    assert "var('cutoff')" not in rendered
    # Nothing calls var('cutoff') any more, so dbt_project.yml declares no var.
    assert answered.variables == ()


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


def test_the_columns_requirement_is_read_off_the_option_not_its_label(monkeypatch):
    """Which option needs columns is the option's own property, and the gate
    that enforces it -- and the code that applies the answer -- must read it
    from there. While both compared against `merge_option().label`, an
    option offering the same choice under any other wording was refused its
    columns and then applied as though it had named none: accepted, and
    silently turned into the model nobody asked for. Renaming the option the
    question offers is the cheapest way to show the two are no longer
    keyed on the string.
    """
    renamed = Option(
        label="key it on a column",
        effect="Each run updates the row whose key matches and inserts the rest.",
        columns_prompt="the column(s) that identify a row uniquely",
    )
    monkeypatch.setattr(tier2_module, "merge_option", lambda keys=(): renamed)
    key = _question_for(convert(ONE_APPEND), "revenue_events").key

    answered = convert(ONE_APPEND, answers={key: Answer("key it on a column", ("order_id",))})
    (model,) = answered.models
    assert (model.incremental_strategy, model.unique_key) == ("merge", ("order_id",))

    # And the same option, answered with nothing to key on, is still refused
    # -- the requirement came off the record, not off the label.
    with pytest.raises(UnknownAnswerError, match="with no columns"):
        convert(ONE_APPEND, answers={key: Answer("key it on a column")})


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
    assert "'append every row' instead" in overridden.action
    # The answer was applied, so the Decision must not hedge as though it
    # had been declined.
    assert "not applied either" not in overridden.action


def test_a_displaced_flag_and_a_declined_answer_are_both_reported_honestly():
    """An answer can displace the blanket flag and then be declined itself --
    the body check refuses a key the model does not select whoever asked for
    it. The dropped flag is still owed a Decision, but that Decision cannot
    read "was answered 'merge on customer_id' instead" beside a model that
    came out a keyless append. A Decision that contradicts the file written
    next to it is the one thing this project does not ship."""
    key = _question_for(convert(ONE_APPEND), "revenue_events").key

    both = convert(
        ONE_APPEND,
        unique_key=("order_id",),
        answers={key: Answer("merge on a unique key", ("customer_id",))},
    )
    (model,) = both.models
    assert (model.incremental_strategy, model.unique_key) == ("append", ())
    (overridden,) = [
        d for d in both.decisions if d.key == "assemble.unique_key_overridden.revenue_events"
    ]
    assert "--unique-key order_id" in overridden.action
    assert "not applied either" in overridden.action
    assert (
        "left as this conversion built it (incremental_strategy='append', no unique_key)"
        in overridden.action
    )
    # The label the caller actually sent, carrying the key it named -- not a
    # label re-derived from the resolved key tuple, which would report
    # "merge on customer_id", an option nobody was offered and nobody chose.
    assert "'merge on a unique key' with customer_id" in overridden.action
    # And the decline itself is still recorded separately, so the two
    # Decisions together account for both inputs.
    assert "assemble.unique_key_not_selected.revenue_events" in {d.key for d in both.decisions}


def test_the_flag_override_is_reported_on_the_merge_branch_too():
    """Both incremental branches route through one emission point, so a
    downgrade that also drops a flag reports the drop the same way -- and the
    flag's usual "kept its script-derived key" Decision gives way to it,
    rather than the two contradicting each other."""
    key = _question_for(convert(ONE_MERGE), "dim_c").key

    both = convert(ONE_MERGE, unique_key=("order_id",), answers={key: Answer("append every row")})
    (model,) = both.models
    assert (model.incremental_strategy, model.unique_key) == ("append", ())
    (overridden,) = [d for d in both.decisions if d.key == "assemble.unique_key_overridden.dim_c"]
    assert "--unique-key order_id" in overridden.action
    assert "'append every row' instead" in overridden.action
    assert "assemble.unique_key_ignored.dim_c" not in {d.key for d in both.decisions}


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
