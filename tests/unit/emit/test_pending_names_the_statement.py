"""The pending list has to say which of her statements is pending.

A reader whose script produced nothing reads this list to find out what the
tool declined. It printed the first line of each statement's text -- which for
any script carrying a comment header is a comment, and for the 129-script
corpus this was measured against printed `--` for one file and the bare word
`SELECT` for another. Neither names a statement.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.emit.test_report import _change

from dbtw.core.context import read_project
from dbtw.core.emit.report import render_report
from dbtw.core.excerpt import LINE_LIMIT, excerpt
from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.ingest.types import StatementKind

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"

HEADED = (
    "--\n"
    "--  Author: someone\n"
    "--  License: see the LICENSE file\n"
    "--\n"
    "-- List unused indexes\n"
    "\n"
    "SELECT s.table_schema, s.table_name, s.index_name\n"
    "FROM information_schema.statistics AS s\n"
    "ORDER BY s.table_schema"
)


def _pending(
    text: str,
    *,
    kind: StatementKind = "select",
    name: str = "mysql_indexes_unused.sql",
    dialect: str = "postgres",
) -> str:
    statement = ClassifiedStatement(
        raw=RawStatement(
            source_file=f"/home/priya/sql/{name}", index=0, text=text, line_start=26, line_end=34
        ),
        kind=kind,
        reason="parsed as a query",
    )
    report = render_report(
        _change(pending=((0, statement),), dialect=dialect),
        read_project(FIXTURES / "jaffle_shop"),
    )
    return report.split("## Still pending")[1].split("\n## ")[0]


def test_the_line_shows_the_query_and_not_its_comment_header() -> None:
    block = _pending(HEADED)

    assert "SELECT s.table_schema" in block
    assert "- **select** — --" not in block, "the header is not the statement"


def test_the_line_names_the_file_and_where_in_it() -> None:
    """One report can hold statements from a whole directory of scripts, so
    the kind alone does not identify one -- and a reader who wants to look at
    it needs somewhere to look.
    """
    block = _pending(HEADED)

    assert "mysql_indexes_unused.sql" in block
    assert "26" in block


def test_a_statement_that_is_only_comments_says_so_rather_than_printing_one() -> None:
    """There is no query to echo, and echoing the comment is what this was
    doing wrong in the first place.
    """
    block = _pending("-- everything here is a comment\n-- and nothing else", kind="unsupported")

    assert "--" not in block.replace("— ", ""), "no comment text is passed off as a statement"
    assert "no SQL" in block


def test_a_long_query_is_cut_rather_than_wrapped_across_the_list() -> None:
    long_query = "SELECT " + ", ".join(f"column_number_{n}" for n in range(40)) + " FROM t"

    block = _pending(long_query)

    (line,) = [row for row in block.splitlines() if row.startswith("- ")]
    assert len(line) < 160
    assert line.rstrip().endswith("…")


def test_a_short_query_is_not_cut() -> None:
    block = _pending("SELECT 1 AS a FROM t")

    (line,) = [row for row in block.splitlines() if row.startswith("- ")]
    assert "SELECT 1 AS a FROM t" in line
    assert "…" not in line


def test_the_kind_is_still_there() -> None:
    """It is what says *why* a reader is looking at this line rather than at a
    model, and the Decisions above explain the kind.
    """
    assert "**select**" in _pending(HEADED)


def test_a_query_that_starts_on_its_own_line_still_shows_its_projection() -> None:
    """`SELECT` alone on the first line is how a great many people write SQL,
    and it was what the corpus this was measured against produced: the list
    read `- **select** — SELECT`. Taking the first line of real SQL fixes the
    comment header and not this, so the statement is collapsed and then cut.
    """
    block = _pending("-- a header\nSELECT\n    host,\n    user\nFROM mysql.user")

    assert "SELECT host, user FROM mysql.user" in block


def test_a_comment_at_the_end_of_a_line_does_not_swallow_the_next_one() -> None:
    """Lines are joined, so a trailing `--` left intact would read as though
    everything after it were commented out -- worse than showing nothing.
    """
    block = _pending("SELECT a, b -- only the good ones\nFROM totals")

    assert "SELECT a, b FROM totals" in block
    assert "only the good ones" not in block


def test_a_double_dash_inside_a_string_is_not_a_comment() -> None:
    block = _pending("SELECT '--not a comment--' AS label\nFROM t")

    assert "'--not a comment--' AS label" in block
    assert "FROM t" in block


def test_a_block_comment_header_is_skipped_too() -> None:
    block = _pending("/* a header\n   over two lines */\nSELECT a FROM t")

    assert "SELECT a FROM t" in block
    assert "a header" not in block


def test_a_hash_comment_header_is_skipped_but_a_temp_table_is_not() -> None:
    """MySQL spells a comment `#`; T-SQL spells a temporary table `#temp`.
    Nothing here knows which dialect it is holding, so `#` counts as a comment
    only at the start of a line -- where it is a comment in the one and not
    valid SQL in the other.
    """
    assert "the header" not in _pending("# the header\nSELECT a FROM t")
    assert "SELECT a FROM #temp" in _pending("SELECT a FROM #temp")


# Every case below was found by a blind review breaking the hand-written
# comment-and-quote scanner this module used to be. Each is one of the two
# failures the module exists to prevent: SQL the reader wrote disappearing
# with no ellipsis to say so, or comment text shown as if it were code.


def test_a_backslash_escaped_quote_does_not_end_the_statement_early() -> None:
    block = _pending(r"SELECT 'it\'s -- not a comment' AS label FROM t", dialect="mysql")

    assert "AS label FROM t" in block, "the rest of the statement must not vanish"


def test_a_string_literal_spanning_two_lines_keeps_what_follows_it() -> None:
    """The scanner's quote state was per line, so the line closing the literal
    looked like a bare comment and the `FROM` on it was dropped in silence.
    """
    excerpted = excerpt("SELECT 'line one\n-- still inside the string' AS x FROM totals")

    assert "AS x FROM totals" in excerpted
    # One line, always. sqlglot returns a literal spanning two lines spanning
    # two lines, and a newline here would break the markdown list this goes
    # into -- the rest of the statement would render as a paragraph of its own.
    assert "\n" not in excerpted


def test_a_dollar_quoted_body_is_not_cut_at_a_dash_inside_it() -> None:
    block = _pending("SELECT $$ this -- is not a comment $$ AS x FROM t")

    assert "AS x FROM t" in block


def test_a_bracketed_identifier_holding_a_dash_survives() -> None:
    block = _pending("SELECT * FROM [my--table]", dialect="tsql")

    assert "my--table" in block, "a T-SQL identifier is not a comment"


def test_a_block_comment_in_the_middle_of_a_line_is_removed_too() -> None:
    """Only a comment starting a line was recognised, so `/* b */` mid-line
    leaked into a list that claims to show the statement.
    """
    block = _pending("/* a */ SELECT /* b */ x FROM t")

    assert "SELECT x FROM t" in block
    assert "/*" not in block


def test_a_nested_block_comment_does_not_leak_its_text() -> None:
    """Postgres nests these. The first `*/` closed the comment early and
    `still outer` was shown as though the reader had written it as SQL.
    """
    block = _pending("/* outer /* inner */ still outer */ SELECT 1 AS a")

    assert "still outer" not in block
    assert "SELECT 1 AS a" in block


def test_a_trailing_hash_comment_does_not_swallow_the_next_line() -> None:
    """MySQL's other comment. The trailing scan only knew `--`, so this read
    as though the `FROM` joined after it were commented out -- the exact
    failure that scan existed to prevent, recreated for `#`.
    """
    block = _pending("SELECT a, b # trailing comment\nFROM totals", dialect="mysql")

    assert "SELECT a, b FROM totals" in block
    assert "trailing comment" not in block


def test_text_that_is_not_sql_shows_the_line_the_reader_wrote() -> None:
    """A MySQL script's client escape. Nothing can parse it, so the fallback
    shows a line as written and claims nothing about what is in it.
    """
    block = _pending('-- a header\n\\! echo "Unused Indexes since startup:"', kind="unsupported")

    assert '\\! echo "Unused Indexes since startup:"' in block


def test_an_unterminated_block_comment_falls_back_rather_than_leaking_it() -> None:
    """Not valid SQL, so no parser will take it. What must not happen is the
    comment's own text being shown as the statement.
    """
    block = _pending("/* opened and never closed\nstill inside\nSELECT 2 FROM t")

    assert "opened and never closed" not in block
    assert "still inside" not in block


def test_the_cut_is_at_the_documented_limit() -> None:
    """`LINE_LIMIT` is the claim; a loose upper bound would let it drift."""
    long_query = "SELECT " + ", ".join(f"column_number_{n}" for n in range(60)) + " FROM t"

    excerpted = excerpt(long_query, "postgres")

    # The number, not the constant read back: `len(excerpted) == LINE_LIMIT`
    # alone passes for any value of LINE_LIMIT, which is a test of nothing.
    assert LINE_LIMIT == 88
    assert len(excerpted) == LINE_LIMIT
    assert excerpted.endswith("…")


def test_the_reader_s_dialect_decides_what_parses() -> None:
    """`DECLARE @d INT = 1` is T-SQL and nothing else parses it. With the
    dialect it is rendered as SQL; without one, no parser takes it and the
    fallback shows the line as written. Both are honest -- what must not
    happen is the dialect the reader gave being dropped on the way here.
    """
    assert excerpt("DECLARE @d INT = 1", "tsql") == "DECLARE @d INTEGER = 1"
    assert excerpt("DECLARE @d INT = 1", None) == "DECLARE @d INT = 1"
