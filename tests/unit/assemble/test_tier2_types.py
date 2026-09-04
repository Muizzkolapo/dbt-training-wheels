from dbtw.core.assemble import ProjectChange, Variable
from dbtw.core.passes import Decision


def test_decision_defaults_keep_tier1_constructions_working():
    d = Decision(
        key="k",
        tier=1,
        action="a",
        reason="r",
        source_file="f.sql",
        line_start=1,
        line_end=1,
    )
    assert d.question == ""
    assert d.chosen == ""
    assert d.alternatives == ()


def test_tier2_decision_carries_question_and_alternatives():
    d = Decision(
        key="k",
        tier=2,
        action="kept @start_date as a dbt var",
        reason="r",
        source_file="f.sql",
        line_start=1,
        line_end=1,
        question="Is start_date a run-time parameter or a constant?",
        chosen="keep as a var",
        alternatives=("inline the literal value",),
    )
    assert d.question
    assert d.chosen == "keep as a var"
    assert "inline the literal value" in d.alternatives


def test_variable_carries_its_default_and_origin():
    v = Variable(name="start_date", default_sql="'2024-01-01'", source_file="e.sql", line_start=3)
    assert v.default_sql == "'2024-01-01'"
    assert v.line_start == 3


def test_uninitialised_variable_has_no_default():
    assert (
        Variable(name="n", default_sql=None, source_file="e.sql", line_start=1).default_sql is None
    )


def test_project_change_variables_defaults_to_empty():
    change = ProjectChange(
        models=(),
        sources=(),
        decisions=(),
        pending=(),
        dialect=None,
        project_name="p",
    )
    assert change.variables == ()
