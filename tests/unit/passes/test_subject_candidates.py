"""`Subject.candidates` -- the columns an append question could be answered
with, so a consumer offering the column picker that question needs reads them
off the Decision instead of inventing one.
"""

from tests.unit.assemble.helpers import convert

from dbtw.core.assemble import ProjectChange
from dbtw.core.passes import Answer, Decision, answer_for, append_option, merge_option
from dbtw.core.passes.tier2 import _projected_columns

PLAIN = "INSERT INTO revenue_events SELECT order_id, amount FROM raw.orders;\n"
# `SELECT "Order_Id"` is the body that makes the round-trip tests below
# discriminating: `assemble` matches an answered key against the model's own
# output columns with `same_identifier`, which stops folding case once either
# side was written quoted.
QUOTED = 'INSERT INTO revenue_events SELECT "Order_Id", amount FROM raw.orders;\n'
# A subquery USING, not the plainer `USING raw.orders s`, so that the merge
# question's empty `candidates` below is evidence of something. A plain-table
# USING becomes the body `SELECT * FROM raw.orders`, whose star makes the
# honest answer empty anyway -- an implementation that collected candidates
# here too would still come out empty and that assertion could never fail. A
# subquery's own SELECT list is what a collector would find.
MERGE = (
    "MERGE INTO revenue_events t USING (SELECT order_id, amount FROM raw.orders) s "
    "ON t.order_id = s.order_id "
    "WHEN MATCHED THEN UPDATE SET t.amount = s.amount "
    "WHEN NOT MATCHED THEN INSERT (order_id, amount) VALUES (s.order_id, s.amount);\n"
)


def _question(change: ProjectChange, table: str) -> Decision:
    (decision,) = [d for d in change.decisions if d.question and table in d.action]
    return decision


def _declines(change: ProjectChange) -> list[str]:
    return [d.key for d in change.decisions if d.key.startswith("assemble.unique_key")]


def test_the_append_question_offers_the_columns_the_model_projects():
    """And keeps them apart from `columns`, which stays empty because no key
    has been named yet -- which is precisely what the question is asking. The
    second assertion is the one that fires if the two fields are ever aliased
    to each other.
    """
    subject = _question(convert(PLAIN), "revenue_events").subject
    assert subject is not None
    assert subject.candidates == ("order_id", "amount")
    assert subject.columns == ()


def test_the_append_questions_merge_options_are_the_ones_that_need_a_column():
    """Why the field exists, asserted rather than narrated: both merge
    spellings this question offers leave their key unsaid, so `assemble`
    refuses either one answered with no columns. Without candidates a consumer
    has nowhere but its own imagination to get one from. If this question ever
    stops offering a keyless option, `candidates` has lost its consumer here
    and this fires.
    """
    decision = _question(convert(PLAIN), "revenue_events")
    assert {o.kind for o in decision.options if o.columns_prompt} == {"merge", "merge_checked"}


def test_a_star_projection_offers_nothing_rather_than_guessing():
    """Empty means "none known", never "none exist". A picker showing two of
    five columns as though they were all of them is the invented-data failure
    in a dropdown.

    The second body is the discriminating one. A bare `SELECT *` parses to no
    names at all, so an implementation that ignored the star flag would still
    come out empty and this test would pass on it. `SELECT o.*, o.amount`
    parses to one name, and offering that one name would describe a model
    whose output row is every column of raw.orders plus `amount` as a model
    with a single column.
    """
    bare = _question(
        convert("INSERT INTO revenue_events SELECT * FROM raw.orders;\n"), "revenue_events"
    ).subject
    assert bare is not None
    assert bare.candidates == ()

    partial = _question(
        convert("INSERT INTO revenue_events SELECT o.*, o.amount FROM raw.orders AS o;\n"),
        "revenue_events",
    ).subject
    assert partial is not None
    assert partial.candidates == ()


def test_a_projection_with_no_output_name_offers_nothing_either():
    """`known_projections`' second flag means the same thing to a picker as
    its first: the list of names is not the whole output row. A bare CASE has
    no output name, so `order_id` is one of this model's two columns, and
    offering it alone would present a partial list as complete.
    """
    subject = _question(
        convert(
            "INSERT INTO revenue_events "
            "SELECT order_id, CASE WHEN amount > 0 THEN 1 ELSE 0 END FROM raw.orders;\n"
        ),
        "revenue_events",
    ).subject
    assert subject is not None
    assert subject.candidates == ()


def test_every_candidate_offered_is_a_key_assemble_accepts():
    """A picker that offers a column the tool then declines is worse than no
    picker. Answering with each candidate's own spelling applies the merge it
    was offered for -- on the quoted body, where a case-insensitive match is
    not enough.
    """
    question = _question(convert(QUOTED), "revenue_events")
    assert question.subject is not None
    assert question.subject.candidates == ("Order_Id", "amount")

    for candidate in question.subject.candidates:
        answer = answer_for(question, "merge", (candidate,))
        answered = convert(QUOTED, answers={question.key: answer})
        (model,) = answered.models
        assert model.incremental_strategy == "merge", candidate
        assert model.unique_key == (candidate,), candidate
        assert _declines(answered) == [], candidate


def test_a_key_typed_from_memory_is_the_decline_this_field_removes():
    """The counterpart to the test above, and the evidence that its passing is
    not free. The same conversion answered with the spelling a user would
    reasonably type -- `order_id`, rather than the `Order_Id` candidates offer
    -- is declined as ambiguous and the model stays an append.
    """
    question = _question(convert(QUOTED), "revenue_events")
    answered = convert(QUOTED, answers={question.key: answer_for(question, "merge", ("order_id",))})
    (model,) = answered.models
    assert model.incremental_strategy == "append"
    assert model.unique_key == ()
    assert _declines(answered) == ["assemble.unique_key_ambiguous.revenue_events"]


def test_the_merge_question_names_its_key_and_offers_no_picker():
    """`columns` is what the script already named; on a MERGE that is the ON
    clause's key, and every option this question offers is spelled with it.
    No option carries a `columns_prompt`, so there is no picker to fill and no
    candidates are collected. The `columns_prompt` assertion is the one that
    fires if a keyless option is ever added here, which is when that decision
    would need remaking.
    """
    decision = _question(convert(MERGE), "revenue_events")
    assert decision.subject is not None
    assert decision.subject.columns == ("order_id",)
    assert [o.kind for o in decision.options if o.columns_prompt] == []
    assert decision.subject.candidates == ()


def test_an_unparseable_body_returns_empty_without_raising():
    """`known_projections` returns None for a body it cannot parse as a query,
    which is a different unknown from a star and must not be folded into one.

    What this checks is only the None branch: that it returns empty instead of
    unpacking None and crashing. It cannot tell "returned () because parsed is
    None" from "returned () after folding None into the star case", because
    both produce (). An append draft's body is always the INSERT's own SELECT
    re-rendered with the dialect it was parsed under, so the pipeline cannot
    reach this branch; it is exercised here rather than left as the one path
    with no test.
    """
    assert _projected_columns("not a query at all ((", None) == ()


def test_an_answered_append_names_the_key_the_answer_supplied():
    """After an answer upgrades an append to a merge, the rebuilt Decision is
    what a screen renders next -- and it has a key now, named in `chosen` and
    in every option's label. `Subject` has to say so too.

    Leaving `columns` empty there put the rebuilt Decision in the one shape
    `Subject`'s own docstring calls an append with no key named yet, on a
    question that is neither. A consumer trusting that reading renders a
    column picker on a question the user has already answered.
    """
    baseline = convert(PLAIN)
    question = _question(baseline, "revenue_events")
    answered = convert(PLAIN, answers={question.key: Answer(merge_option().label, ("order_id",))})

    rebuilt = [d for d in answered.decisions if d.key == question.key][0]
    assert rebuilt.subject is not None
    assert rebuilt.subject.columns == ("order_id",)
    # And the projections are still the model's own, unchanged by answering.
    assert rebuilt.subject.candidates == ("order_id", "amount")
    assert "order_id" in rebuilt.chosen


def test_declining_the_key_leaves_the_subject_naming_no_key():
    """The mirror case, so the assertion above is about the answer and not
    about rebuilding in general: answering "append every row" names no key, so
    there is none for `columns` to carry.
    """
    baseline = convert(PLAIN)
    question = _question(baseline, "revenue_events")
    answered = convert(PLAIN, answers={question.key: Answer(append_option().label)})

    rebuilt = [d for d in answered.decisions if d.key == question.key][0]
    assert rebuilt.subject is not None
    assert rebuilt.subject.columns == ()
