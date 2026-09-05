"""`dbtw convert --unique-key` — answers the append-or-merge question.

A bare INSERT...SELECT converts to an append incremental with no way to know
from the SQL alone whether it's safe to re-run (tier2.append_pass). This flag
lets a human answer that: every append model becomes a merge on the given
columns. A model that is already a merge — its key read straight off its own
MERGE ON clause — is never overridden; that key outranks a blanket flag.
"""

from pathlib import Path

import pytest

from dbtw.cli.main import _build_parser, main

ROOT = Path(__file__).parents[2] / "fixtures"
PROJECT = ROOT / "projects" / "jaffle_shop"

APPEND_ONLY_SQL = "INSERT INTO revenue_events\nSELECT order_id, amount FROM stg_orders;\n"

APPEND_MULTI_COL_SQL = (
    "INSERT INTO revenue_events\nSELECT order_id, order_date, amount FROM stg_orders;\n"
)

MERGE_ONLY_SQL = (
    "MERGE INTO dim_customers AS t USING stg_customers AS s "
    "ON t.customer_id = s.customer_id WHEN MATCHED THEN UPDATE SET t.name = s.name;\n"
)

COMBINED_SQL = APPEND_ONLY_SQL + "\n" + MERGE_ONLY_SQL


def _run(tmp_path, sql_dir, sql_text, *extra):
    sql_dir.mkdir(exist_ok=True)
    sql_file = sql_dir / "in.sql"
    sql_file.write_text(sql_text, encoding="utf-8")
    return main(
        ["convert", str(sql_file), "--project", str(PROJECT), "--out", str(tmp_path), *extra]
    )


def _find(tmp_path, name_fragment: str) -> str:
    (match,) = [p for p in tmp_path.rglob("*.sql") if name_fragment in p.name]
    return match.read_text()


def test_help_text_answers_the_append_or_merge_question(capsys):
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["convert", "--help"])
    # argparse wraps help text at word boundaries (including mid-hyphen), so
    # compare with whitespace collapsed out entirely rather than searching
    # for a phrase that could legitimately be split across two lines.
    out = "".join(capsys.readouterr().out.split())
    assert "append-or-mergequestion" in out
    assert "INSERT...SELECT" in out


def test_without_the_flag_append_model_is_unchanged(tmp_path):
    assert _run(tmp_path, tmp_path / "sql", APPEND_ONLY_SQL) == 0
    body = _find(tmp_path, "revenue_events")
    assert "incremental_strategy='append'" in body
    assert "merge" not in body
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "Chose: append every row" in report
    assert "(alternatives: merge on a unique key)" in report


def test_flag_upgrades_append_but_leaves_a_script_derived_merge_key_alone(tmp_path):
    assert _run(tmp_path, tmp_path / "sql", COMBINED_SQL, "--unique-key", "order_id") == 0

    upgraded = _find(tmp_path, "revenue_events")
    assert "incremental_strategy='merge'" in upgraded
    assert "unique_key='order_id'" in upgraded

    untouched = _find(tmp_path, "dim_customers")
    assert "incremental_strategy='merge'" in untouched
    assert "unique_key='customer_id'" in untouched
    assert "order_id" not in untouched

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    # the upgraded model's Decision: chosen becomes "merge on <keys>", the
    # append option demoted to an alternative
    assert "Chose: merge on order_id" in report
    assert "(alternatives: append every row)" in report
    # the untouched model's Decision names both keys and says the flag lost
    assert (
        "dim_customers kept its script-derived unique_key (customer_id); "
        "--unique-key order_id was not applied" in report
    )


def test_flag_matching_the_scripts_own_merge_key_makes_no_change_and_no_extra_decision(tmp_path):
    assert _run(tmp_path, tmp_path / "sql", MERGE_ONLY_SQL, "--unique-key", "customer_id") == 0
    body = _find(tmp_path, "dim_customers")
    assert "unique_key='customer_id'" in body

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "was not applied" not in report
    assert "kept its script-derived" not in report


def test_comma_separated_unique_key_produces_a_composite_key(tmp_path):
    assert (
        _run(
            tmp_path, tmp_path / "sql", APPEND_MULTI_COL_SQL, "--unique-key", "order_id, order_date"
        )
        == 0
    )
    body = _find(tmp_path, "revenue_events")
    assert "incremental_strategy='merge'" in body
    assert "unique_key=['order_id', 'order_date']" in body


def test_a_single_unique_key_column_renders_as_a_bare_string_not_a_list(tmp_path):
    assert _run(tmp_path, tmp_path / "sql", APPEND_ONLY_SQL, "--unique-key", "order_id") == 0
    body = _find(tmp_path, "revenue_events")
    assert "unique_key='order_id'" in body
    assert "unique_key=['order_id']" not in body


def _decision_block(report: str, source_line: int) -> str:
    """The question-bearing Decision (the "became an incremental model" one,
    "Chose: ..." sub-line included) for the statement at `source_line` in
    the fixture's "in.sql".

    A redefined target gets TWO Decisions attributed to the same line: the
    plain "redefinition of X — kept the last definition" collision note (no
    question, no sub-line) and the actual "became an incremental model"
    Decision this helper is after. Skipping any match with no Question
    sub-line is what tells them apart — picking the first line-number match
    unconditionally would grab the collision note instead.
    """
    marker = f"in.sql:{source_line})"
    lines = report.splitlines()
    for i, line in enumerate(lines):
        if marker not in line:
            continue
        if i + 1 < len(lines) and lines[i + 1].strip().startswith("- Question:"):
            return line + "\n" + lines[i + 1]
    raise AssertionError(f"no question-bearing Decision recorded for in.sql:{source_line}")


REDEFINED_APPEND_SQL = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders_v1;\n"
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders_v2;\n"
)


def test_redefinition_without_the_flag_keeps_the_surviving_statements_own_decision(tmp_path):
    """Baseline, no --unique-key: two INSERTs into the same bare target, the
    later one wins (dbt keeps one file per model). This must already hold
    with no flag involved at all -- it pins down which statement "the
    surviving one" means for the flagged case right below.
    """
    assert _run(tmp_path, tmp_path / "sql", REDEFINED_APPEND_SQL) == 0
    body = _find(tmp_path, "revenue_events")
    assert "stg_orders_v2" in body
    assert "stg_orders_v1" not in body

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "Chose: append every row" in _decision_block(report, 2)


def test_redefinition_upgrades_the_surviving_statement_not_the_discarded_one(tmp_path):
    """Two INSERTs into the same bare target: append_pass records a full
    "became an incremental model" Decision for BOTH statements (only a
    "superseded" verdict skips that -- a "redefinition" does not), even
    though only the later one survives into the rendered model. The
    Decision that gets upgraded to merge must be the surviving statement's,
    never the discarded one's -- otherwise the report credits a statement
    that was overwritten while the Decision for the model that's actually
    on disk still claims "append", flatly contradicting the rendered
    config.
    """
    assert _run(tmp_path, tmp_path / "sql", REDEFINED_APPEND_SQL, "--unique-key", "order_id") == 0

    body = _find(tmp_path, "revenue_events")
    assert "stg_orders_v2" in body  # confirms which statement actually survived
    assert "incremental_strategy='merge'" in body

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    surviving = _decision_block(report, 2)  # the v2 statement -- this IS the model
    discarded = _decision_block(report, 1)  # the v1 statement -- overwritten, not a model

    assert "incremental_strategy='merge'" in surviving
    assert "Chose: merge on order_id" in surviving

    # the discarded statement's Decision must stay exactly as append_pass
    # left it -- it was never this model, so it must never claim the upgrade
    assert "incremental_strategy='append'" in discarded
    assert "Chose: append every row" in discarded


NOT_SELECTED_SQL = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"
    "INSERT INTO page_views SELECT session_id, ts FROM some_source;\n"
)


def test_flag_is_not_applied_to_a_model_that_does_not_select_the_column(tmp_path):
    """--unique-key is a blanket, per-conversion flag -- it must not be
    forced onto every append model regardless of what that model actually
    selects. page_views (session_id, ts) never selects order_id; applying
    unique_key='order_id' to it anyway would compile a merge that fails at
    dbt run time (unique_key must be an output column). revenue_events, in
    the same run, does select order_id and must still be upgraded.
    """
    assert _run(tmp_path, tmp_path / "sql", NOT_SELECTED_SQL, "--unique-key", "order_id") == 0

    upgraded = _find(tmp_path, "revenue_events")
    assert "incremental_strategy='merge'" in upgraded
    assert "unique_key='order_id'" in upgraded

    left_alone = _find(tmp_path, "page_views")
    assert "incremental_strategy='append'" in left_alone
    assert "unique_key" not in left_alone

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "page_views" in report
    assert "order_id" in report
    assert "not applied" in report
    assert "does not select" in report


STAR_SQL = "INSERT INTO revenue_events SELECT * FROM stg_orders;\n"


def test_flag_is_applied_through_a_star_projection_but_flagged_as_unverified(tmp_path):
    """A star projection means the columns actually selected can't be read
    off the model's own body -- --unique-key is still applied (the brief's
    "blanket" default), but the Decision must say the column could not be
    confirmed, instead of silently implying the same certainty as the
    column-list case above.
    """
    assert _run(tmp_path, tmp_path / "sql", STAR_SQL, "--unique-key", "order_id") == 0
    body = _find(tmp_path, "revenue_events")
    assert "incremental_strategy='merge'" in body
    assert "unique_key='order_id'" in body

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "Chose: merge on order_id" in report
    # "*" alone would trivially match report.py's own "**bold**" markdown;
    # the actual signal is the caveat naming the star projection by name.
    assert "selects *" in report
    assert "could not be verified" in report


def test_unique_key_with_no_incremental_models_records_a_no_op_decision(tmp_path):
    """A typo'd --unique-key (or one supplied against SQL with no append or
    merge candidates at all) must not be a silent no-op -- the flag was
    read, and the report should say plainly that nothing needed it.
    """
    plain_select = "SELECT 1 AS a;\n"
    assert _run(tmp_path, tmp_path / "sql", plain_select, "--unique-key", "order_id") == 0
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "--unique-key" in report
    assert "order_id" in report
    assert "no model" in report.lower() or "no incremental" in report.lower()


CASE_SQL = (
    "INSERT INTO revenue_events\n"
    "SELECT order_id, CASE WHEN amount > 0 THEN 1 ELSE 0 END FROM stg_orders;\n"
)

UNION_SQL = (
    "INSERT INTO revenue_events\n"
    "SELECT order_id, amount FROM stg_orders\n"
    "UNION ALL\n"
    "SELECT order_id, amount FROM stg_payments;\n"
)

QUOTED_SQL = 'INSERT INTO revenue_events\nSELECT "Order_Id", amount FROM stg_orders;\n'


def test_flag_matches_the_column_case_insensitively(tmp_path):
    """A CLI-supplied --unique-key value can never carry quoting, so it's
    always an unquoted identifier -- and unquoted identifiers fold
    case-insensitively in every dialect. ORDER_ID and order_id are the same
    column; the flag must not be defeated by a mere case mismatch.
    """
    assert _run(tmp_path, tmp_path / "sql", APPEND_ONLY_SQL, "--unique-key", "ORDER_ID") == 0
    body = _find(tmp_path, "revenue_events")
    assert "incremental_strategy='merge'" in body
    assert "unique_key='ORDER_ID'" in body

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "Chose: merge on ORDER_ID" in report
    assert "not applied" not in report
    assert "does not select" not in report


def test_flag_is_verified_through_an_unaliased_case_projection(tmp_path):
    """order_id is a plain, named column in this SELECT -- it is positively
    verified regardless of the sibling CASE expression having no name of
    its own. This must apply with NO caveat: there is no star anywhere in
    this SQL, so a caveat claiming one would be a fabrication.
    """
    assert _run(tmp_path, tmp_path / "sql", CASE_SQL, "--unique-key", "order_id") == 0
    body = _find(tmp_path, "revenue_events")
    assert "incremental_strategy='merge'" in body
    assert "unique_key='order_id'" in body

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "Chose: merge on order_id" in report
    assert "selects *" not in report
    assert "could not" not in report


def test_flag_is_verified_through_a_union_body(tmp_path):
    """A UNION ALL body's columns are fully knowable via sqlglot's
    named_selects (it works on exp.Query, not just exp.Select) -- there is
    no star here either, so no caveat may appear.
    """
    assert _run(tmp_path, tmp_path / "sql", UNION_SQL, "--unique-key", "order_id") == 0
    body = _find(tmp_path, "revenue_events")
    assert "incremental_strategy='merge'" in body
    assert "unique_key='order_id'" in body

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "Chose: merge on order_id" in report
    assert "selects *" not in report
    assert "could not" not in report


def test_flag_still_applies_with_a_caveat_when_the_body_is_a_star_projection(tmp_path):
    """Regression: a genuine star projection must still get the caveat --
    findings 5/6 must not remove the case that finding 2 introduced it for.
    """
    assert _run(tmp_path, tmp_path / "sql", STAR_SQL, "--unique-key", "order_id") == 0
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "Chose: merge on order_id" in report
    assert "selects *" in report
    assert "could not be verified" in report


def test_a_quoted_output_name_that_only_differs_by_case_is_left_ambiguous(tmp_path):
    """The model's own SELECT projects a quoted "Order_Id" -- same_identifier
    is case-sensitive once either side is quoted, so this is genuinely
    unknowable from the SQL text alone (it depends on how this warehouse
    folds unquoted identifiers). The flag must not be silently applied
    (might be wrong) nor silently rejected with "does not select" (might be
    right) -- it must be left alone with a Decision naming the ambiguity,
    the same tri-state naming.compare_targets already uses for this exact
    kind of unknowable-from-the-text-alone case.
    """
    assert _run(tmp_path, tmp_path / "sql", QUOTED_SQL, "--unique-key", "order_id") == 0
    body = _find(tmp_path, "revenue_events")
    assert "incremental_strategy='append'" in body
    assert "unique_key" not in body

    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "Order_Id" in report
    assert "ambiguous" in report.lower()
    assert "does not select" not in report
