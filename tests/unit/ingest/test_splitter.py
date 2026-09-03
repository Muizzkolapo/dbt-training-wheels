from dbtw.core.ingest.splitter import split_sql


def test_splits_on_semicolons():
    spans = split_sql("SELECT 1;\nSELECT 2;\n")
    assert [s.text for s in spans] == ["SELECT 1", "SELECT 2"]


def test_semicolon_inside_string_does_not_split():
    spans = split_sql("SELECT 'a;b' AS s;\nSELECT 2")
    assert len(spans) == 2
    assert spans[0].text == "SELECT 'a;b' AS s"


def test_trailing_statement_without_semicolon_is_kept():
    spans = split_sql("SELECT 1;\nSELECT 2")
    assert [s.text for s in spans] == ["SELECT 1", "SELECT 2"]


def test_leading_comment_attaches_to_following_statement():
    spans = split_sql("SELECT 1;\n-- explains the next one\nSELECT 2;")
    assert len(spans) == 2
    assert spans[1].text.startswith("-- explains the next one")
    assert spans[1].text.endswith("SELECT 2")


def test_procedure_body_semicolons_do_not_split():
    sql = "CREATE PROCEDURE p AS BEGIN SELECT 1; SELECT 2; END; SELECT 3"
    spans = split_sql(sql, dialect="tsql")
    assert len(spans) == 2
    assert spans[0].text.startswith("CREATE PROCEDURE")
    assert "SELECT 2" in spans[0].text
    assert spans[1].text == "SELECT 3"


def test_case_end_does_not_eat_a_real_split():
    sql = "SELECT CASE WHEN 1 = 1 THEN 'a' END AS c;\nSELECT 2"
    spans = split_sql(sql)
    assert len(spans) == 2


def test_line_numbers_are_one_based_and_span_the_statement():
    spans = split_sql("SELECT 1;\n\n-- note\nSELECT\n  2;\n")
    assert (spans[0].line_start, spans[0].line_end) == (1, 1)
    assert (spans[1].line_start, spans[1].line_end) == (3, 5)


def test_empty_and_whitespace_input_yield_no_spans():
    assert split_sql("") == []
    assert split_sql("   \n\n  ") == []


def test_untokenizable_input_falls_back_to_one_span():
    spans = split_sql("SELECT 'unterminated")
    assert len(spans) == 1
    assert spans[0].text == "SELECT 'unterminated"
