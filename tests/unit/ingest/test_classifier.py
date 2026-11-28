import pytest

from dbtw.core.ingest import RawStatement
from dbtw.core.ingest.classifier import classify
from dbtw.core.ingest.types import StatementKind


def _raw(text: str) -> RawStatement:
    return RawStatement(source_file="t.sql", index=0, text=text, line_start=1, line_end=1)


def _kind(text: str, dialect: str | None = None) -> str:
    return classify(_raw(text), dialect).kind


def test_select_and_cte_select():
    assert _kind("SELECT a FROM t") == "select"
    assert _kind("WITH c AS (SELECT 1 AS x) SELECT * FROM c") == "select"


def test_leading_comment_does_not_change_kind():
    assert _kind("-- explains it\nSELECT a FROM t") == "select"


def test_ctas_variants():
    assert _kind("CREATE TABLE x AS SELECT a FROM t") == "create_table_as"
    assert _kind("CREATE OR REPLACE TABLE x AS SELECT 1 AS a", "snowflake") == "create_table_as"
    assert _kind("CREATE TEMP TABLE x AS SELECT 1 AS a") == "create_table_as"
    assert _kind("SELECT a INTO #tmp FROM t", "tsql") == "create_table_as"


def test_bare_create_table_is_ddl_other():
    assert _kind("CREATE TABLE x (a INT)") == "ddl_other"


def test_create_view():
    assert _kind("CREATE OR REPLACE VIEW v AS SELECT a FROM t") == "create_view"


def test_query_less_create_view_is_ddl_other():
    # probe-verified: sqlglot 30.18.0 parses "CREATE VIEW v" to
    # Create(kind="VIEW", expression=None) — schema-only DDL, not a query.
    assert _kind("CREATE VIEW v") == "ddl_other"


def test_insert_select_with_and_without_columns():
    assert _kind("INSERT INTO x SELECT a FROM t") == "insert_select"
    assert _kind("INSERT INTO x (a) SELECT a FROM t") == "insert_select"


def test_insert_values_is_unsupported_with_reason():
    stmt = classify(_raw("INSERT INTO x VALUES (1, 'a')"))
    assert stmt.kind == "unsupported"
    assert "VALUES" in stmt.reason


def test_merge():
    assert (
        _kind("MERGE INTO x USING y ON x.id = y.id WHEN MATCHED THEN UPDATE SET a = y.a") == "merge"
    )


def test_parse_error_is_unsupported_with_reason():
    stmt = classify(_raw("SELEC a FRM t"))
    assert stmt.kind == "unsupported"
    assert stmt.reason  # carries the parse error text


def test_reason_is_always_populated():
    for text in ["SELECT 1", "CREATE TABLE x AS SELECT 1 AS a", "SELEC nope"]:
        assert classify(_raw(text)).reason


def test_dml_kinds():
    assert _kind("DELETE FROM x WHERE a > 1") == "delete"
    assert _kind("UPDATE x SET a = 1 WHERE b = 2") == "update"
    assert _kind("TRUNCATE TABLE x") == "truncate"


def test_grant():
    assert _kind("GRANT SELECT ON x TO reporting_role") == "grant"


def test_revoke_is_grant_kind():
    # RFC: kind "grant" covers both GRANT and REVOKE (permissions management).
    assert _kind("REVOKE SELECT ON x FROM r") == "grant"


def test_variables():
    assert _kind("DECLARE @d DATE = '2024-01-01'", "tsql") == "variable"
    assert _kind("SET @x = 5", "tsql") == "variable"


def test_session_settings():
    assert _kind("SET search_path = analytics", "postgres") == "session"
    assert _kind("USE analytics", "tsql") == "session"
    assert _kind("ALTER SESSION SET QUERY_TAG = 'x'", "snowflake") == "session"


def test_ddl_other():
    assert _kind("CREATE INDEX ix ON x (a)") == "ddl_other"
    assert _kind("ALTER TABLE x ADD COLUMN b INT") == "ddl_other"
    assert _kind("DROP TABLE IF EXISTS x") == "ddl_other"


def test_procedural():
    assert _kind("CREATE PROCEDURE p AS BEGIN SELECT 1; END", "tsql") == "procedural"
    assert _kind("EXEC p", "tsql") == "procedural"


def test_command_fallback_is_unsupported():
    stmt = classify(_raw("PRINT 'hi'"), "tsql")
    assert stmt.kind == "unsupported"
    assert "could not parse this syntax" in stmt.reason
    assert "PRINT" in stmt.reason  # the raw text is carried in the reason


def test_copy_is_unsupported():
    stmt = classify(_raw("COPY INTO t FROM @stage"), "snowflake")
    assert stmt.kind == "unsupported"
    assert "COPY loads files" in stmt.reason


def test_union_is_select():
    assert _kind("SELECT 1 UNION SELECT 2") == "select"


def test_ctas_with_union_body():
    assert _kind("CREATE TABLE x AS SELECT 1 AS a UNION ALL SELECT 2 AS a") == "create_table_as"


def test_ctas_with_parenthesized_query_body():
    assert _kind("CREATE TABLE x AS (SELECT 1 AS a)", "snowflake") == "create_table_as"


def test_insert_select_with_union_body():
    assert _kind("INSERT INTO x SELECT a FROM t UNION SELECT a FROM u") == "insert_select"


def test_transaction_control_is_unsupported():
    begin_stmt = classify(_raw("BEGIN"), "postgres")
    assert begin_stmt.kind == "unsupported"
    assert "transaction control" in begin_stmt.reason
    commit_stmt = classify(_raw("COMMIT"), "postgres")
    assert commit_stmt.kind == "unsupported"
    assert "transaction control" in commit_stmt.reason


def test_snowflake_set_variable_is_variable_not_session():
    assert _kind("SET x = 5", "snowflake") == "variable"


def test_top_level_if_is_procedural():
    assert _kind("IF @x = 1 SELECT 1", "tsql") == "procedural"
    assert _kind("IF OBJECT_ID('x') IS NOT NULL DROP TABLE x", "tsql") == "procedural"


_TOTALITY_CASES: list[tuple[str, str | None, str]] = [
    ("SELECT a FROM t", None, "select"),
    ("CREATE TABLE x AS SELECT a FROM t", None, "create_table_as"),
    ("CREATE VIEW v AS SELECT a FROM t", None, "create_view"),
    ("INSERT INTO x SELECT a FROM t", None, "insert_select"),
    ("MERGE INTO x USING y ON x.id = y.id WHEN MATCHED THEN UPDATE SET a = y.a", None, "merge"),
    ("DELETE FROM x", None, "delete"),
    ("UPDATE x SET a = 1", None, "update"),
    ("TRUNCATE TABLE x", None, "truncate"),
    ("DECLARE @d INT = 1", "tsql", "variable"),
    ("USE analytics", "tsql", "session"),
    ("GRANT SELECT ON x TO r", None, "grant"),
    ("DROP TABLE x", None, "ddl_other"),
    ("EXEC p", "tsql", "procedural"),
    ("SELEC nope", None, "unsupported"),
]


@pytest.mark.parametrize(("sql", "dialect", "expected"), _TOTALITY_CASES)
def test_every_kind_is_reachable(sql, dialect, expected):
    assert _kind(sql, dialect) == expected


def test_totality_cases_cover_every_kind():
    import typing

    covered = {expected for _, _, expected in _TOTALITY_CASES}
    assert covered == set(typing.get_args(StatementKind))


# A Postgres regex match. With no dialect, sqlglot stops at the `!` of `!~`.
# The comment of the statement is on line 30 of its file, and its SQL starts
# on line 31. The `!` is on the third line of the text, so the reader must
# look at line 32 of the file.
_POSTGRES_ONLY = "-- users by name\nSELECT name\nFROM users WHERE name !~ '^x' AND id NOT IN (1, 2)"


def _unparseable(text: str = _POSTGRES_ONLY, dialect: str | None = None) -> str:
    first_sql_line = 31 if text.startswith("--") else 30
    raw = RawStatement(
        source_file="t.sql",
        index=3,
        text=text,
        line_start=first_sql_line,
        line_end=30 + text.count("\n"),
    )
    stmt = classify(raw, dialect)
    assert stmt.kind == "unsupported"
    return stmt.reason


def test_an_unparseable_reason_has_no_terminal_colour_codes():
    reason = _unparseable()

    assert "\x1b" not in reason
    assert "[4m" not in reason
    assert "[0m" not in reason


def test_an_unparseable_reason_gives_the_line_in_the_file_and_the_token():
    reason = _unparseable()

    assert "could not parse" in reason
    assert "line 32" in reason, "the line is counted from the top of the file"
    assert "line 3," not in reason, "not from the top of the statement"
    assert "`!`" in reason


def test_an_unparseable_reason_gives_the_column_when_the_line_is_not_the_first():
    # Line 3 of the statement is all of line 32 of the file, so the column
    # that sqlglot counts is also the column in the file. `!` is column 23.
    assert "column 23" in _unparseable()


def test_an_unparseable_reason_gives_no_column_on_the_first_line_of_the_statement():
    # The statement can start after another statement on the same line of
    # the file, so its column 1 is not always column 1 of the file.
    reason = _unparseable("SELECT a FROM t WHERE x !~ 'y'")

    assert "line 30" in reason
    assert "column" not in reason
    assert "`!`" in reason


def test_an_unparseable_reason_keeps_the_detail_from_sqlglot():
    reason = _unparseable()

    assert "Invalid expression / Unexpected token" in reason
    assert "Line 3, Col" not in reason, "the statement-relative position is not repeated"


def test_an_unparseable_reason_names_the_dialect_option_when_no_dialect_was_given():
    reason = _unparseable()

    assert "start dbtw again with the --dialect option" in reason, "convert and web both take it"
    assert "`--dialect postgres`" in reason
    assert "may use the syntax of one database" in reason


def test_the_dialect_advice_does_not_claim_the_dialect_is_the_cause():
    # A plain typo fails to parse too. The advice is a condition, not a cause.
    reason = _unparseable("SELECT 1 FROM")

    assert "If the SQL is correct, it may use the syntax of one database." in reason
    assert "The SQL may use" not in reason


def test_an_unparseable_reason_names_the_dialect_that_was_given():
    reason = _unparseable(dialect="tsql")

    assert "as the tsql dialect" in reason
    assert "`--dialect postgres`" not in reason, "the example is for a run with no dialect"
    assert "start dbtw again with its name in the --dialect option" in reason


def test_a_statement_sqlglot_cannot_tokenize_still_gets_a_clean_reason():
    # An unterminated string fails in the tokenizer, with no line or token.
    reason = _unparseable("SELECT 'abc")

    assert "could not parse the statement that starts at line 30" in reason
    assert "\x1b" not in reason
    assert "\n" not in reason
    assert "--dialect" in reason


def test_a_token_object_in_the_sqlglot_detail_is_shown_as_its_text():
    # sqlglot puts the repr of its Token object in some messages.
    reason = _unparseable("SELECT 1 FROM")

    assert "<Token" not in reason
    assert "TokenType" not in reason
    assert "the end of the statement" in reason


def test_a_python_class_in_the_sqlglot_detail_is_shown_as_its_name():
    # sqlglot writes "missing for <class 'sqlglot.expressions.core.EQ'>".
    reason = _unparseable("SELECT * FROM t WHERE a = = 1")

    assert "<class" not in reason
    assert "sqlglot.expressions" not in reason
    assert "`Required keyword: 'expression' missing for EQ`" in reason


def test_an_escape_byte_in_the_token_does_not_reach_the_reason():
    # The token is the SQL text of the reader. An ESC byte in it would be a
    # terminal code on the screen, the same as the codes sqlglot adds.
    reason = _unparseable("SELECT a FROM t WHERE \x1b[4m\x1b")

    assert "\x1b" not in reason
    assert "line 30" in reason


@pytest.mark.parametrize(
    ("text", "dialect"),
    [
        (_POSTGRES_ONLY, None),
        (_POSTGRES_ONLY, "tsql"),
        ("SELECT a FROM t WHERE x !~ 'y'", None),
        ("SELECT 'abc", None),
        ("SELECT 1 FROM", None),
        ("SELECT * FROM t WHERE a = = 1", None),
    ],
)
def test_an_unparseable_reason_follows_the_structural_rules_of_ste(text, dialect):
    from tests.unit.test_ste_wording import findings

    assert findings(_unparseable(text, dialect)) == []


def test_an_unparseable_statement_after_a_comment_names_its_line_in_the_file(tmp_path):
    """The splitter cites the first SQL line of a statement, after its
    leading comment. sqlglot counts lines from the top of the text, which
    includes that comment. The reason must still name the line in the file.
    """
    from dbtw.core.ingest import classify_statements, ingest

    script = tmp_path / "users.sql"
    script.write_text(
        "SELECT 1;\n-- users by name\n-- second note\nSELECT name\nFROM users WHERE name !~ 'x'",
        encoding="utf-8",
    )

    stmt = classify_statements(ingest(script))[1]

    assert stmt.raw.line_start == 4, "the cited line is the first SQL line"
    assert stmt.kind == "unsupported"
    assert "line 5" in stmt.reason, stmt.reason
