from dbtw.core.assemble.variables import extract_variables
from dbtw.core.ingest import ClassifiedStatement, RawStatement


def _stmt(text: str, kind: str = "variable", index: int = 0) -> ClassifiedStatement:
    raw = RawStatement(source_file="e.sql", index=index, text=text, line_start=3, line_end=3)
    return ClassifiedStatement(raw=raw, kind=kind, reason="test")  # type: ignore[arg-type]


def test_declare_with_default():
    vars_, consumed = extract_variables(
        ((0, _stmt("DECLARE @start_date DATE = '2024-01-01'")),), "tsql"
    )
    assert [(v.name, v.default_sql) for v in vars_] == [("start_date", "'2024-01-01'")]
    assert consumed == (0,)
    assert vars_[0].line_start == 3


def test_declare_without_default_has_none():
    vars_, _ = extract_variables(((0, _stmt("DECLARE @n INT")),), "tsql")
    assert vars_[0].default_sql is None


def test_one_declare_can_hold_several_variables():
    vars_, consumed = extract_variables(((0, _stmt("DECLARE @a INT = 1, @b INT = 2")),), "tsql")
    assert [v.name for v in vars_] == ["a", "b"]
    assert consumed == (0,)


def test_set_of_a_script_variable_is_a_variable():
    vars_, consumed = extract_variables(((0, _stmt("SET @x = 5")),), "tsql")
    assert [(v.name, v.default_sql) for v in vars_] == [("x", "5")]
    assert consumed == (0,)


def test_session_set_is_not_a_variable():
    vars_, consumed = extract_variables(
        ((0, _stmt("SET search_path = analytics", kind="session")),), "postgres"
    )
    assert vars_ == ()
    assert consumed == ()


def test_non_variable_kinds_are_ignored():
    vars_, consumed = extract_variables(((0, _stmt("SELECT 1", kind="select")),), None)
    assert (vars_, consumed) == ((), ())


def test_unparseable_variable_statement_is_not_consumed():
    vars_, consumed = extract_variables(((0, _stmt("DECLARE @@@ bad")),), "tsql")
    assert consumed == ()


def test_shared_type_comma_declare_keeps_every_variable():
    vars_, consumed = extract_variables(((0, _stmt("DECLARE @a, @b INT")),), "tsql")
    assert [v.name for v in vars_] == ["a", "b"]
    assert consumed == (0,)


def test_shared_type_comma_declare_shares_the_default():
    vars_, _ = extract_variables(((0, _stmt("DECLARE @a, @b INT = 7")),), "tsql")
    assert [(v.name, v.default_sql) for v in vars_] == [("a", "7"), ("b", "7")]


def test_set_variable_syntax_is_a_variable():
    vars_, consumed = extract_variables(((0, _stmt("SET VARIABLE x = 5")),), "duckdb")
    assert [(v.name, v.default_sql) for v in vars_] == [("x", "5")]
    assert consumed == (0,)


def test_plain_session_set_is_still_ignored():
    vars_, consumed = extract_variables(
        ((0, _stmt("SET search_path = analytics", kind="session")),), "postgres"
    )
    assert (vars_, consumed) == ((), ())
