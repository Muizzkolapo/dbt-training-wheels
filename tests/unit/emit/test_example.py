from dbtw.core.assemble import assemble
from dbtw.core.assemble.types import AssembledModel
from dbtw.core.context import read_project
from dbtw.core.emit.example import _parseable_projections, worked_example
from dbtw.core.ingest import classify_statements, ingest
from dbtw.core.passes import run_passes
from dbtw.core.passes.types import Decision, Subject


def _conversion():
    ir = ingest("tests/fixtures/sql/incremental_etl.sql", None)
    return (
        assemble(
            run_passes(classify_statements(ir), ir.dialect),
            read_project("tests/fixtures/projects/jaffle_shop"),
        ),
        ir.dialect,
    )


def _model(body: str, strategy: str | None, unique_key: tuple[str, ...]) -> AssembledModel:
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
        source_indices=(1,),
        incremental_strategy=strategy,
        unique_key=unique_key,
    )


def _question(chosen: str, columns: tuple[str, ...]) -> Decision:
    return Decision(
        key="tier2.merge.customers.sql:1",
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
    for row in example.before + example.script_after + example.model_after:
        for cell in row:
            assert cell.startswith("<") and cell.endswith(">"), cell


def test_the_append_example_leaves_more_rows_behind_than_the_script_does():
    """The point of the example: two behaviours, visibly different. If both
    sides render the same rows the example teaches nothing."""
    change, dialect = _conversion()
    dec = next(d for d in change.decisions if d.question and "append" in d.chosen)
    model = next(m for m in change.models if "events" in m.name)
    example = worked_example(dec, model, dialect)
    assert example is not None
    assert len(example.model_after) > len(example.script_after)


def test_the_merge_example_updates_a_row_where_the_append_example_copies_it():
    """The merge branch, which no fixture reaches: the only merge model in
    the fixtures is a SELECT * and so has no example at all. Both shapes are
    built from one body here so the difference is the strategy and nothing
    else."""
    body = "SELECT\n  customer_id,\n  email\nFROM {{ source('raw', 'customers') }}"
    merge = worked_example(
        _question("merge on customer_id", ("customer_id",)),
        _model(body, "merge", ("customer_id",)),
        None,
    )
    append = worked_example(
        _question("append every row", ()),
        _model(body, "append", ()),
        None,
    )
    assert merge is not None
    assert append is not None

    existing = ("<customer_id>", "<email>")
    arriving = ("<customer_id-new>", "<email-new>")

    assert merge.key == "customer_id"
    assert merge.before == (existing,)
    assert merge.script_after == (existing,)
    assert merge.model_after == (existing, arriving)

    assert append.key == ""
    assert append.model_after == (existing, existing, arriving)

    # The whole teaching point: merge leaves one copy of the row that was
    # already there, append leaves two.
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


def test_a_projection_that_is_jinja_has_no_example():
    """A rewritten `{{ var(...) }}` in the SELECT list has no output name
    this engine can know. Reporting the stand-in as a column name would
    print something the user's table does not contain."""
    body = "SELECT\n  customer_id,\n  {{ var('cutoff') }}\nFROM {{ source('raw', 'customers') }}"
    assert (
        worked_example(_question("append every row", ()), _model(body, "append", ()), None) is None
    )


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

    assert (
        worked_example(_question("append every row", ()), _model(body, "append", ()), None) is None
    )


def test_a_body_that_does_not_parse_has_no_example():
    """A Jinja block statement is not neutralised and never will be. The
    column list is unknown, so there is no example."""
    body = "{% if is_incremental() %}\nSELECT customer_id FROM t\n{% endif %}"
    assert (
        worked_example(_question("append every row", ()), _model(body, "append", ()), None) is None
    )


def test_a_decision_with_no_question_has_no_example():
    change, dialect = _conversion()
    dec = next(d for d in change.decisions if not d.question)
    model = change.models[0]
    assert worked_example(dec, model, dialect) is None


def test_a_question_with_no_subject_has_no_example():
    """A Tier-2 question that never recorded what it is about cannot be
    illustrated: there is no table for the example to be about."""
    import dataclasses

    dec = dataclasses.replace(_question("append every row", ()), subject=None)
    body = "SELECT\n  customer_id\nFROM {{ source('raw', 'customers') }}"
    assert worked_example(dec, _model(body, "append", ()), None) is None
