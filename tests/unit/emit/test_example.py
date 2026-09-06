import dataclasses

from dbtw.core.assemble import assemble
from dbtw.core.assemble.types import AssembledModel
from dbtw.core.context import read_project
from dbtw.core.emit.example import _parseable_projections, worked_example
from dbtw.core.ingest import classify_statements, ingest
from dbtw.core.passes import run_passes
from dbtw.core.passes.types import Decision, Subject

_BODY = "SELECT\n  customer_id,\n  email\nFROM {{ source('raw', 'customers') }}"

_EXISTING = ("<customer_id>", "<email>")
_UPDATED = ("<customer_id>", "<email-new>")
_INSERTED = ("<customer_id-new>", "<email-new>")


def _conversion():
    ir = ingest("tests/fixtures/sql/incremental_etl.sql", None)
    return (
        assemble(
            run_passes(classify_statements(ir), ir.dialect),
            read_project("tests/fixtures/projects/jaffle_shop"),
        ),
        ir.dialect,
    )


def _model(
    body: str = _BODY,
    strategy: str | None = "append",
    unique_key: tuple[str, ...] = (),
    source_indices: tuple[int, ...] = (1,),
) -> AssembledModel:
    """A model built here rather than read off a fixture, so a branch the
    fixtures do not reach can still be asserted against."""
    return AssembledModel(
        name="stg_dim_customers",
        path="models/staging/stg_dim_customers.sql",
        body=body,
        materialization="incremental",
        grants=(),
        layer="staging",
        depends_on=(),
        leading_comments=(),
        source_indices=source_indices,
        incremental_strategy=strategy,
        unique_key=unique_key,
    )


def _question(
    chosen: str = "append every row", columns: tuple[str, ...] = (), index: int = 1
) -> Decision:
    return Decision(
        key=f"tier2.merge.customers.sql:{index}",
        tier=2,
        action="dim_customers becomes an incremental model",
        reason="the script's MERGE names a key",
        source_file="customers.sql",
        line_start=1,
        line_end=4,
        question="does customer_id uniquely identify a row in dim_customers?",
        chosen=chosen,
        subject=Subject(table="dim_customers", columns=columns),
    )


def test_the_example_uses_the_model_s_real_column_names():
    """Column names must be the ones the model actually selects. Anything
    else would be describing a table the user does not have."""
    change, dialect = _conversion()
    dec = next(d for d in change.decisions if d.question and "append" in d.chosen)
    model = next(m for m in change.models if "events" in m.name)
    example = worked_example(dec, model, dialect)
    assert example is not None
    assert example.columns == ("event_id", "occurred_at")


def test_values_are_placeholders_not_invented_data():
    """The engine never reads the warehouse. A value that looks like real
    data is a lie about the user's table."""
    change, dialect = _conversion()
    dec = next(d for d in change.decisions if d.question and "append" in d.chosen)
    model = next(m for m in change.models if "events" in m.name)
    example = worked_example(dec, model, dialect)
    assert example is not None

    rows = example.before + example.model_after
    # Without this the loop below is vacuous: no rows, no assertions.
    assert len(example.before) == 1
    assert len(example.model_after) == 3
    assert all(len(row) == len(example.columns) for row in rows)

    for row in rows:
        for cell in row:
            assert cell.startswith("<") and cell.endswith(">"), cell


def test_the_append_example_leaves_more_rows_behind_than_were_there_before():
    """The point of the example: a behaviour the reader can see. If the table
    looks the same before and after, the example teaches nothing."""
    change, dialect = _conversion()
    dec = next(d for d in change.decisions if d.question and "append" in d.chosen)
    model = next(m for m in change.models if "events" in m.name)
    example = worked_example(dec, model, dialect)
    assert example is not None
    assert len(example.model_after) > len(example.before)


def test_the_merge_example_updates_a_row_where_the_append_example_copies_it():
    """The merge branch, which no fixture reaches: the only merge model in
    the fixtures is a SELECT * and so has no example at all. Both shapes are
    built from one body here so the difference is the strategy and nothing
    else."""
    merge = worked_example(
        _question("merge on customer_id", ("customer_id",)),
        _model(strategy="merge", unique_key=("customer_id",)),
        None,
    )
    append = worked_example(_question(), _model(strategy="append"), None)
    assert merge is not None
    assert append is not None

    assert merge.key == "customer_id"
    assert merge.before == (_EXISTING,)
    assert merge.model_after == (_UPDATED, _INSERTED)

    assert append.key == ""
    assert append.before == (_EXISTING,)
    assert append.model_after == (_EXISTING, _EXISTING, _INSERTED)

    # The update the merge option's own effect text promises: the key holds
    # its value while the rest of the row moves. Without this the "matched"
    # row is byte-identical to the one already there and nothing is shown.
    assert _UPDATED[0] == _EXISTING[0]
    assert _UPDATED[1] != _EXISTING[1]

    # And the duplication the append option promises: the same row twice.
    assert append.model_after[0] == append.model_after[1]

    # Merge leaves the row it matched; append leaves it and a copy.
    assert len(merge.model_after) < len(append.model_after)


def test_a_body_that_projects_a_star_has_no_example():
    """dim_customers is SELECT *, so the real column list is unknown. An
    example listing guessed columns would be invented."""
    change, dialect = _conversion()
    dec = next(d for d in change.decisions if d.question and "customer_id" in d.question)
    model = next(m for m in change.models if "customers" in m.name)
    assert worked_example(dec, model, dialect) is None


def test_the_star_body_is_refused_for_its_star_and_not_for_failing_to_parse():
    """The refusal above passes if the body is unreadable for any reason. It
    must be the star that stops it, or the module is not doing its job."""
    change, dialect = _conversion()
    model = next(m for m in change.models if "customers" in m.name)
    known = _parseable_projections(model.body, dialect)
    assert known is not None, "the body did not parse; the refusal is for the wrong reason"
    projections, has_star, has_unnamed = known
    assert has_star is True
    assert has_unnamed is False
    assert projections == []


def test_an_unaliased_jinja_projection_is_refused_for_being_unnameable():
    """A rewritten `{{ var(...) }}` in the SELECT list has no output name
    this engine can know. Reporting the stand-in as a column name would
    print something the user's table does not contain."""
    body = "SELECT\n  customer_id,\n  {{ var('cutoff') }}\nFROM {{ source('raw', 'customers') }}"

    # The refusal must come from the stand-in surfacing as an output name,
    # not from the body having stopped parsing: a regressed substitution
    # would return None here too and this test would stay green.
    neutral = _parseable_projections(body.replace("{{ var('cutoff') }}", "cutoff"), None)
    assert neutral is not None
    assert [name for name, _quoted in neutral[0]] == ["customer_id", "cutoff"]

    assert _parseable_projections(body, None) is None
    assert worked_example(_question(), _model(body), None) is None


def test_an_aliased_jinja_projection_contributes_its_alias():
    """The one case where a Jinja tag legitimately names a column: the alias
    is written in the body, so it owes nothing to run time and is real."""
    body = (
        "SELECT\n  customer_id,\n  {{ var('cutoff') }} AS cutoff\n"
        "FROM {{ source('raw', 'customers') }}"
    )
    known = _parseable_projections(body, None)
    assert known is not None
    projections, has_star, has_unnamed = known
    assert has_star is False
    assert has_unnamed is False
    assert [name for name, _quoted in projections] == ["customer_id", "cutoff"]

    example = worked_example(_question(), _model(body), None)
    assert example is not None
    assert example.columns == ("customer_id", "cutoff")


def test_a_projection_with_no_output_name_has_no_example():
    """A bare CASE with no alias is a real output column whose name the SQL
    never states. Listing the columns around it would show a narrower row
    than the model actually builds -- inventing by omission."""
    body = (
        "SELECT\n  customer_id,\n  CASE WHEN spend > 100 THEN 1 ELSE 0 END\n"
        "FROM {{ source('raw', 'customers') }}"
    )
    known = _parseable_projections(body, None)
    assert known is not None
    projections, has_star, has_unnamed = known
    assert has_star is False
    assert has_unnamed is True
    assert [name for name, _quoted in projections] == ["customer_id"]

    assert worked_example(_question(), _model(body), None) is None


def test_a_body_that_does_not_parse_has_no_example():
    """A Jinja block statement is not neutralised and never will be. The
    column list is unknown, so there is no example."""
    body = "{% if is_incremental() %}\nSELECT customer_id FROM t\n{% endif %}"
    assert _parseable_projections(body, None) is None
    assert worked_example(_question(), _model(body), None) is None


def test_a_merge_model_with_no_unique_key_has_no_example():
    """Nothing to match on, so the merge branch cannot be drawn -- and the
    append branch must not be drawn under a merge config instead."""
    assert worked_example(_question(), _model(strategy="merge", unique_key=()), None) is None


def test_a_merge_model_that_does_not_select_its_key_has_no_example():
    """The key is what holds still while the rest of the row moves. A key the
    model does not select cannot hold still, and the updated row would come
    back identical to the inserted one -- a merge shown updating nothing."""
    assert (
        worked_example(_question(), _model(strategy="merge", unique_key=("order_id",)), None)
        is None
    )


def test_a_model_that_is_not_incremental_has_no_example():
    """Neither branch describes a table dbt rebuilds from scratch each run."""
    assert worked_example(_question(), _model(strategy=None), None) is None


def test_a_decision_from_another_statement_has_no_example():
    """Task 4 pairs Decisions to models. A mispairing must yield nothing, not
    a confident example of the wrong table."""
    model = _model(source_indices=(1,))
    assert worked_example(_question(index=9), model, None) is None
    # Not vacuous: the same decision keyed to this model's own statement does
    # produce one.
    assert worked_example(_question(index=1), model, None) is not None


def test_a_decision_with_no_statement_in_its_key_has_no_example():
    """`assemble.rename.<name>` and friends answer for a model, not for a
    statement, so there is no index to pair on."""
    dec = dataclasses.replace(_question(), key="assemble.rename.dim_customers")
    assert worked_example(dec, _model(), None) is None


def test_a_decision_with_no_question_has_no_example():
    change, dialect = _conversion()
    dec = next(d for d in change.decisions if not d.question)
    model = change.models[0]
    assert worked_example(dec, model, dialect) is None


def test_a_question_with_no_subject_has_no_example():
    """A Tier-2 question that never recorded what it is about cannot be
    illustrated: there is no table for the example to be about."""
    dec = dataclasses.replace(_question(), subject=None)
    assert worked_example(dec, _model(), None) is None
