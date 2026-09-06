import pytest

from dbtw.core.passes.types import (
    append_option,
    inline_option,
    merge_option,
    var_option,
)

# Terms the persona walkthroughs recorded as undefined for people who have
# never used dbt (spec section 11.3). The plain register exists to say the
# same thing without them, so its wording must contain none of these.
JARGON = (
    "incremental",
    "materialized",
    "materialization",
    "unique_key",
    "ref(",
    "source(",
    "jinja",
    "dbt run",
    "dbt build",
    "dbt test",
    "dbt compile",
    "warehouse",
    "staging",
    "config(",
    "{{",
)

ALL_OPTIONS = [
    append_option(),
    merge_option(),
    merge_option(("order_id",)),
    inline_option(),
    var_option("cutoff"),
]


@pytest.mark.parametrize("option", ALL_OPTIONS, ids=lambda o: o.label)
def test_every_option_carries_a_plain_wording(option):
    assert option.plain, f"{option.label} has no plain wording"
    assert option.plain != option.effect


@pytest.mark.parametrize("option", ALL_OPTIONS, ids=lambda o: o.label)
def test_the_plain_wording_uses_no_dbt_vocabulary(option):
    lowered = option.plain.lower()
    found = [term for term in JARGON if term in lowered]
    assert not found, f"{option.label}'s plain wording uses {found}"


def test_the_dbt_native_effect_is_still_allowed_its_vocabulary():
    """The registers are for different readers. This asserts they really are
    different -- if `effect` ever stops naming dbt's own mechanics, the plain
    register has quietly replaced it instead of sitting beside it."""
    assert "unique_key" in merge_option().effect


def test_the_plain_append_wording_names_the_consequence_a_newcomer_cares_about():
    """Not a tautology check: this fails if the wording is softened to
    something that no longer says what goes wrong."""
    assert "twice" in append_option().plain or "duplicate" in append_option().plain


def test_every_question_and_option_in_a_real_conversion_has_a_plain_form():
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
        assert d.plain_question, f"{d.key} has no plain question"
        lowered = d.plain_question.lower()
        assert not [t for t in JARGON if t in lowered], f"{d.key}: {d.plain_question}"
        for option in d.options:
            assert option.plain, f"{d.key} offers {option.label} with no plain wording"
