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
    # The comment on line 3 belongs to the second statement's text. The
    # statement itself starts on line 4, where its SQL starts.
    spans = split_sql("SELECT 1;\n\n-- note\nSELECT\n  2;\n")
    assert (spans[0].line_start, spans[0].line_end) == (1, 1)
    assert (spans[1].line_start, spans[1].line_end) == (4, 5)


def test_empty_and_whitespace_input_yield_no_spans():
    assert split_sql("") == []
    assert split_sql("   \n\n  ") == []


def test_untokenizable_input_falls_back_to_one_span():
    spans = split_sql("SELECT 'unterminated")
    assert len(spans) == 1
    assert spans[0].text == "SELECT 'unterminated"


def test_begin_without_transaction_keyword_does_not_glue_the_file():
    sql = "BEGIN;\nUPDATE x SET a = 1;\nCOMMIT;\nSELECT 1;\nSELECT 2;"
    spans = split_sql(sql, dialect="postgres")
    assert len(spans) == 5


def test_begin_transaction_keyword_does_not_glue_the_file():
    sql = "BEGIN TRANSACTION;\nSELECT 1;\nSELECT 2"
    spans = split_sql(sql, dialect="tsql")
    assert len(spans) == 3


def test_nested_begin_case_end_proc_still_keeps_body_whole():
    sql = "CREATE PROCEDURE p AS BEGIN SELECT CASE WHEN 1=1 THEN 'a' END; END; SELECT 9"
    spans = split_sql(sql, dialect="tsql")
    assert len(spans) == 2


def test_a_leading_comment_stays_in_the_text_but_the_line_is_the_first_sql_line():
    """The demo's nightly.sql has its header comment on line 1 and the INSERT
    on line 2. The comment stays in the text, because it becomes the leading
    comment of the model. The line that a Decision cites is the INSERT.
    """
    spans = split_sql("-- nightly load\nINSERT INTO t\nSELECT 1;\n\n/* two\nlines */\nSELECT 2;")

    assert spans[0].text.startswith("-- nightly load")
    assert (spans[0].line_start, spans[0].line_end) == (2, 3)
    assert spans[1].text.startswith("/* two")
    assert (spans[1].line_start, spans[1].line_end) == (7, 7)


def test_a_trailing_comment_after_the_last_statement_is_not_a_statement():
    """A comment alone is not a statement. Emitted as one, it was classified
    as a statement that could not be parsed, and it showed as pending work.
    """
    spans = split_sql("SELECT 1;\n\n-- trailing note\n-- and more")

    assert [s.text for s in spans] == ["SELECT 1"]
    assert (spans[0].line_start, spans[0].line_end) == (1, 1)


def test_a_trailing_comment_after_a_statement_with_no_semicolon_stays_in_it():
    """With no semicolon, the comment is inside the last statement's text,
    and the last statement is still emitted."""
    spans = split_sql("SELECT 1;\nSELECT 2 -- the last one")

    assert [s.text for s in spans] == ["SELECT 1", "SELECT 2 -- the last one"]


def test_input_of_only_comments_yields_no_spans():
    assert split_sql("-- one note\n/* and a block\n comment */\n") == []


def test_a_comment_before_an_empty_statement_is_not_a_statement():
    """`;` with only a comment before it closes nothing. The statements on
    each side are kept, at their own lines."""
    spans = split_sql("SELECT 1;\n-- stray\n;\nSELECT 2;")

    assert [s.text for s in spans] == ["SELECT 1", "SELECT 2"]
    assert (spans[1].line_start, spans[1].line_end) == (4, 4)


def test_a_comment_inside_a_statement_is_kept_in_its_text():
    spans = split_sql("SELECT 1 -- one\nFROM t /* note */;\n-- lead\nSELECT 2")

    assert [s.text for s in spans] == ["SELECT 1 -- one\nFROM t /* note */", "-- lead\nSELECT 2"]
