from pathlib import Path

import pytest

from dbtw.cli.main import main

ROOT = Path(__file__).parents[2] / "fixtures"
SQL = ROOT / "sql" / "etl_script.sql"
PROJECT = ROOT / "projects" / "jaffle_shop"


def _run(tmp_path, *extra: str) -> int:
    return main(["convert", str(SQL), "--project", str(PROJECT), "--out", str(tmp_path), *extra])


def _run_against(sql_path, out_dir, *extra: str) -> int:
    return main(
        ["convert", str(sql_path), "--project", str(PROJECT), "--out", str(out_dir), *extra]
    )


def test_convert_writes_models_and_a_report(tmp_path, capsys):
    assert _run(tmp_path, "--dialect", "tsql") == 0
    report = tmp_path / "CONVERSION_REPORT.md"
    assert report.is_file()
    written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.sql")}
    assert "models/staging/stg_daily_revenue.sql" in written
    assert "models/staging/stg_dim_customers.sql" in written
    out = capsys.readouterr().out
    assert "2 models" in out
    assert "CONVERSION_REPORT.md" in out


def test_converted_model_content_is_dbt_shaped(tmp_path):
    _run(tmp_path, "--dialect", "tsql")
    revenue = (tmp_path / "models" / "staging" / "stg_daily_revenue.sql").read_text()
    assert revenue.startswith(
        "{{ config(\n    materialized='table'\n) }}"
    )  # staging default is view
    assert "raw_orders" in revenue
    assert "TRUNCATE" not in revenue.upper()
    customers = (tmp_path / "models" / "staging" / "stg_dim_customers.sql").read_text()
    assert "grants={'select': ['reporting']}" in customers
    assert "INTO" not in customers.upper()


def test_report_records_the_variable_as_a_var_and_the_dropped_use(tmp_path):
    _run(tmp_path, "--dialect", "tsql")
    report = (tmp_path / "CONVERSION_REPORT.md").read_text()
    assert "start_date" in report  # the DECLARE was extracted and rewritten to a dbt var
    assert "Nothing — every statement was handled." in report  # no longer pending
    assert "profiles.yml" in report  # the USE was dropped with its reason
    assert "Table references and script variables have been rewritten" in report


def test_unknown_dialect_exits_two(tmp_path, capsys):
    assert _run(tmp_path, "--dialect", "sqlserver") == 2
    assert "sqlserver" in capsys.readouterr().err


def test_missing_project_exits_two(tmp_path, capsys):
    code = main(["convert", str(SQL), "--project", str(tmp_path / "nope"), "--out", str(tmp_path)])
    assert code == 2
    assert capsys.readouterr().err


def test_no_arguments_exits_two(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_ingest_warnings_are_printed_to_stderr(tmp_path, capsys):
    """A non-UTF-8 .sql file is skipped by ingest() and recorded as a warning
    (ingestor.py). The CLI must surface it — silently dropping an entire
    input file while still exiting 0 is exactly the kind of untruthful
    report the project's reporting rule forbids.
    """
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    (sql_dir / "good.sql").write_text("SELECT 1 AS a", encoding="utf-8")
    bad_file = sql_dir / "bad.sql"
    bad_file.write_bytes(b"SELECT 1 AS a -- \xff\xfe not valid utf-8 \x80")
    out_dir = tmp_path / "out"

    code = _run_against(sql_dir, out_dir, "--dialect", "tsql")
    assert code == 0
    err = capsys.readouterr().err
    assert "warning:" in err
    assert "bad.sql" in err


def test_out_path_pointing_at_an_existing_file_exits_two_not_a_traceback(tmp_path, capsys):
    out_as_file = tmp_path / "out"
    out_as_file.write_text("occupied", encoding="utf-8")
    code = _run_against(SQL, out_as_file, "--dialect", "tsql")
    assert code == 2
    assert capsys.readouterr().err


def test_a_quoted_identifier_that_escapes_out_dir_exits_two_not_a_traceback(tmp_path, capsys):
    """A quoted identifier like "../../deep_escape" survives ingestion and
    naming untouched, and only trips emit's out-of-out_dir guard at write
    time. That guard is correct to refuse the write — but the refusal is
    input-driven (a quoted identifier in the source SQL), not a dbtw bug,
    and must exit 2 like every other bad-input case, not crash with a
    traceback.
    """
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    (sql_dir / "escape.sql").write_text(
        "CREATE TABLE base_t AS SELECT 1 AS a;\n"
        'CREATE TABLE "../../deep_escape" AS SELECT a FROM base_t;\n',
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    code = _run_against(sql_dir, out_dir, "--dialect", "tsql")
    assert code == 2
    assert capsys.readouterr().err


# --- a TRUNCATE and the INSERT that repopulates it are one full rebuild, and
# recognising them as a pair is what keeps the rebuild a rebuild. Both passes
# that pair them matched the two targets as raw strings, so a case difference
# between the two spellings -- the same table in every dialect -- broke the
# pair. What that cost showed up two passes later, in Decisions that
# contradicted each other in the same report.


def _report(tmp_path, sql: str, *extra: str) -> str:
    sql_file = tmp_path / "in.sql"
    sql_file.write_text(sql, encoding="utf-8")
    out = tmp_path / "out"
    assert _run_against(sql_file, out, *extra) == 0
    return (out / "CONVERSION_REPORT.md").read_text()


def _models(tmp_path) -> dict[str, str]:
    return {p.name: p.read_text() for p in (tmp_path / "out").rglob("*.sql")}


REBUILD_MIXED_CASE = (
    "TRUNCATE TABLE Rebuild_t;\nINSERT INTO rebuild_t\nSELECT x, y FROM raw.src_t;\n"
)


def test_a_rebuild_pairs_across_a_case_difference_in_the_target(tmp_path):
    """`TRUNCATE TABLE Rebuild_t` and `INSERT INTO rebuild_t` name one table:
    unquoted identifiers fold in every dialect sqlglot supports. The pair is a
    full rebuild, which is dbt's table materialization."""
    report = _report(tmp_path, REBUILD_MIXED_CASE)
    (body,) = [b for name, b in _models(tmp_path).items() if "rebuild_t" in name]
    assert "materialized='table'" in body
    assert "incremental" not in body
    assert "**Pending statements**: 0" in report


def test_an_unpaired_rebuild_never_becomes_an_append(tmp_path):
    """The failure this guards: with the pair unrecognised, the TRUNCATE was
    dropped and its INSERT was swept up as an append incremental -- a script
    that wipes and repopulates became a table that only ever grows, with
    nothing in the report saying so. Even if no pair forms, an INSERT whose
    target a TRUNCATE in the same file names must never be converted to an
    append."""
    report = _report(tmp_path, REBUILD_MIXED_CASE)
    assert "incremental_strategy='append'" not in report
    for body in _models(tmp_path).values():
        assert "incremental_strategy='append'" not in body


AMBIGUOUS_REBUILD = (
    "TRUNCATE TABLE analytics.rebuild_t;\nINSERT INTO rebuild_t\nSELECT x, y FROM raw.src_t;\n"
)


def test_a_rebuild_whose_two_halves_qualify_differently_is_deferred_not_appended(tmp_path):
    """`analytics.rebuild_t` and bare `rebuild_t` may or may not be the same
    table -- it depends on the session's default schema, which the script does
    not record. That is not licence to append: the INSERT is left pending with
    a Decision, the same treatment a DELETE+INSERT pair gets."""
    report = _report(tmp_path, AMBIGUOUS_REBUILD)
    assert "incremental_strategy='append'" not in report
    assert "TRUNCATE" in report
    assert "**Pending statements**: 0" not in report


STAR_REBUILD = "TRUNCATE TABLE rebuild_t;\nINSERT INTO rebuild_t (a, b)\nSELECT * FROM raw.src_t;\n"


def test_a_deferred_column_list_rebuild_does_not_also_claim_the_truncate_was_solo(tmp_path):
    """The pair IS found -- the report says so, twice -- and then cannot be
    mapped because a star projection hides the column count. Dropping the
    TRUNCATE at that point with "no surviving INSERT pair" contradicts the
    Decision printed beside it. Both halves stay pending together."""
    report = _report(tmp_path, STAR_REBUILD)
    assert "cannot map columns positionally" in report
    assert "no surviving INSERT pair" not in report
    assert "dropped solo TRUNCATE" not in report


def test_a_genuinely_solo_truncate_is_still_dropped_and_says_why(tmp_path):
    """The control. A TRUNCATE with no INSERT against its target anywhere in
    the file really has no dbt equivalent, and the reason stays true."""
    report = _report(
        tmp_path, "TRUNCATE TABLE orphan_t;\nINSERT INTO other_t SELECT x FROM raw.s;\n"
    )
    assert "dropped solo TRUNCATE" in report
    assert "no surviving INSERT pair" in report


# --- one table, two spellings. Every pass that compares a target now goes
# through naming.py, but the places a *draft* is matched -- the collision
# upsert, the grant attach, and the assembler's final-name dedup -- still
# compared draft.name as raw text. So two spellings of one table became two
# drafts, the report announced both, and the second file overwrote the first
# on any case-insensitive filesystem.

CASE_DUP_SQL = (
    "MERGE INTO EVENTS AS t USING raw.events_corrections AS s ON t.event_id = s.event_id "
    "WHEN MATCHED THEN UPDATE SET t.event_date = s.event_date "
    "WHEN NOT MATCHED THEN INSERT (event_id, event_date) VALUES (s.event_id, s.event_date);\n"
    "INSERT INTO Events SELECT event_id, event_date FROM raw.events_stage;\n"
)


def test_two_spellings_of_one_target_become_one_model(tmp_path):
    """`EVENTS` and `Events` are one unquoted table in every dialect. Two
    drafts meant the report claimed two models while the filesystem held one
    -- and the one it held was the second definition, so the MERGE's whole
    conversion, unique_key included, vanished with nothing recorded."""
    report = _report(tmp_path, CASE_DUP_SQL)
    written = list(_models(tmp_path))
    assert len(written) == 1, written
    assert report.count("| stg_") == 1, report
    assert "**Models**: 1" in report


def test_the_second_definition_of_a_two_spelling_target_is_recorded(tmp_path):
    """Whichever statement becomes the model, the report has to say the other
    one existed and was not converted. Silence here is how the merge config
    disappeared. Both spellings appear, because both were written."""
    report = _report(tmp_path, CASE_DUP_SQL)
    assert "several statements" in report
    assert "EVENTS" in report
    assert "Events" in report


def test_a_grant_matches_its_model_across_a_case_difference(tmp_path):
    """`GRANT SELECT ON orders` names the table `CREATE TABLE Orders` just
    built. Dropping it with "references an object this conversion doesn't
    create" contradicts the model file written in the same run."""
    report = _report(
        tmp_path,
        "CREATE TABLE Orders AS SELECT id, amount FROM raw.orders;\n"
        "GRANT SELECT ON orders TO analyst;\n",
    )
    assert "this conversion doesn't create" not in report
    (body,) = [b for name, b in _models(tmp_path).items() if "rders" in name]
    assert "grants" in body
    assert "analyst" in body


def test_a_column_list_rebuild_pairs_across_a_case_difference_too(tmp_path):
    """Tier 1 finds this pair, records "left for that pass", and hands it to
    tier 2 -- which keyed on the target's raw text and so never re-found it,
    leaving both statements pending under a promise the report had already
    made. The bare-INSERT pairing was fixed without its column-list sibling."""
    report = _report(
        tmp_path,
        "TRUNCATE TABLE Rebuild_t;\nINSERT INTO rebuild_t (a, b)\nSELECT x, y FROM raw.src_t;\n",
    )
    (body,) = [b for name, b in _models(tmp_path).items() if "rebuild_t" in name]
    assert "materialized='table'" in body
    assert "x AS a" in body
    assert "y AS b" in body
    assert "**Pending statements**: 0" in report


def test_a_quoted_spelling_is_not_folded_into_an_unquoted_one(tmp_path):
    """The control, and the other half of the rule: a quoted identifier's case
    is significant, so `"Events"` and `events` are two different tables. They
    are two drafts -- but a dbt model is one file and these two want names that
    differ only in case, so the collision is recorded rather than left to the
    filesystem to resolve."""
    report = _report(
        tmp_path,
        'INSERT INTO "Events" SELECT id FROM raw.a;\nINSERT INTO events SELECT id FROM raw.b;\n',
    )
    assert len(_models(tmp_path)) == 1
    assert "differing only in case" in report
    assert "kept" in report


# --- a target this script already rebuilds. append_pass's TRUNCATE guard
# scanned `pending`, but tier 1 consumes the TRUNCATE the moment it pairs with
# the first INSERT -- so a SECOND insert into the same target saw no pending
# TRUNCATE, converted to an append, and replaced the rebuild draft outright.
# The materialization inverted from table to append, the first INSERT's whole
# SELECT disappeared, and the tier-1 Decision announcing the table model was
# left standing over a file that no longer matched it.

WIPE_THEN_TWO_INSERTS = (
    "TRUNCATE TABLE events;\n"
    "INSERT INTO events SELECT id FROM raw.staging_events_full;\n"
    "INSERT INTO events SELECT id FROM raw.staging_events_extra;\n"
)


def test_a_second_insert_does_not_replace_the_rebuild_it_adds_to(tmp_path):
    """Three statements are one rebuild loaded from two sources. dbt has no
    model that appends to itself mid-build, and combining the two SELECTs into
    a UNION the script never wrote would be an invention -- so the rebuild
    stands and the second INSERT is left for a human."""
    report = _report(tmp_path, WIPE_THEN_TWO_INSERTS)
    (body,) = [b for name, b in _models(tmp_path).items() if "events" in name]
    assert "materialized='table'" in body
    assert "incremental_strategy='append'" not in body
    assert "staging_events_full" in body
    assert "**Pending statements**: 0" not in report


def test_the_dropped_insert_of_a_rebuild_is_named_not_silently_absorbed(tmp_path):
    """Whatever happens to the second INSERT, its source table must not vanish
    from the record -- it disappeared entirely before, appearing in no model,
    no dependency, no pending entry and no Decision."""
    report = _report(tmp_path, WIPE_THEN_TWO_INSERTS)
    assert "staging_events_extra" in report
    assert "already" in report or "rebuild" in report


def test_the_tier_one_rebuild_decision_still_describes_the_file_written(tmp_path):
    """The Decision saying the pair became a table model has to stay true of
    what is on disk."""
    report = _report(tmp_path, WIPE_THEN_TWO_INSERTS)
    assert "became one model (materialized='table')" in report
    (body,) = [b for name, b in _models(tmp_path).items() if "events" in name]
    assert "materialized='table'" in body


def test_an_insert_into_a_created_table_does_not_turn_it_into_an_append(tmp_path):
    """The same shape without a TRUNCATE: `CREATE TABLE t AS ...` then
    `INSERT INTO t ...` builds one table from two statements. Converting the
    INSERT alone discards the CREATE's SELECT just the same."""
    report = _report(
        tmp_path,
        "CREATE TABLE totals AS SELECT id FROM raw.a;\nINSERT INTO totals SELECT id FROM raw.b;\n",
    )
    (body,) = [b for name, b in _models(tmp_path).items() if "totals" in name]
    assert "materialized='table'" in body
    assert "raw" in body or "a" in body
    assert "incremental" not in body
    assert "raw.b" in report or "b " in report


def test_a_merge_into_a_target_this_script_rebuilds_is_deferred_too(tmp_path):
    """A MERGE has the same effect on a rebuild draft as an append does, and
    merge_pass never checked for one at all."""
    report = _report(
        tmp_path,
        "TRUNCATE TABLE dim_c;\n"
        "INSERT INTO dim_c SELECT id, city FROM raw.src;\n"
        "MERGE INTO dim_c AS t USING raw.updates AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n",
    )
    (body,) = [b for name, b in _models(tmp_path).items() if "dim_c" in name]
    assert "materialized='table'" in body
    assert "incremental_strategy='merge'" not in body
    assert "**Pending statements**: 0" not in report


def test_a_second_column_list_insert_into_a_rebuild_says_why_it_was_left(tmp_path):
    """The bare-INSERT sibling of this explains itself; the column-list path
    declined the statement and said nothing, leaving it in "Still pending"
    with no reason beside it. Same shape, same answer, same Decision owed."""
    report = _report(
        tmp_path,
        "TRUNCATE TABLE t;\n"
        "INSERT INTO t (a, b) SELECT x, y FROM raw.s1;\n"
        "INSERT INTO t (a, b) SELECT x, y FROM raw.s2;\n",
    )
    (body,) = [
        b for name, b in _models(tmp_path).items() if "_t.sql" in name or name == "stg_t.sql"
    ]
    assert "materialized='table'" in body
    assert "s1" in body
    assert "several statements" in report
    assert "raw.s2" in report or "s2" in report


# --- statements spread across several files. The guard that keeps a rebuild
# from being replaced is deliberately NOT file-scoped, unlike the checks that
# PAIR two statements into one model: pairing is a claim about adjacency,
# while replacing a rebuild with an append destroys the rebuild's SELECT
# whether or not the two statements share a file. What it must not do is
# describe the relationship as one within a single script when it isn't.


def _report_dir(tmp_path, files: dict[str, str], *extra: str) -> str:
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    for name, text in files.items():
        (sql_dir / name).write_text(text, encoding="utf-8")
    out = tmp_path / "out"
    assert _run_against(sql_dir, out, *extra) == 0
    return (out / "CONVERSION_REPORT.md").read_text()


REBUILD_FILE = (
    "TRUNCATE TABLE orders_summary;\n"
    "INSERT INTO orders_summary (order_id, total) SELECT order_id, total FROM raw.orders;\n"
)
APPEND_FILE = "INSERT INTO orders_summary SELECT order_id, total FROM raw.late_orders;\n"


def test_a_rebuild_in_another_file_still_blocks_the_append_that_would_replace_it(tmp_path):
    """Two files, one target. The append still must not replace the rebuild
    -- the loss is the same across files as within one."""
    _report_dir(tmp_path, {"01_rebuild.sql": REBUILD_FILE, "02_append.sql": APPEND_FILE})
    (body,) = [b for name, b in _models(tmp_path).items() if "orders_summary" in name]
    assert "materialized='table'" in body
    assert "incremental_strategy='append'" not in body


def test_the_cross_file_deferral_does_not_claim_the_two_share_a_script(tmp_path):
    """ "this script already rebuilds" is false of a rebuild in a different
    file. The Decision has to describe the relationship it actually found."""
    report = _report_dir(tmp_path, {"01_rebuild.sql": REBUILD_FILE, "02_append.sql": APPEND_FILE})
    deferral = [line for line in report.splitlines() if "several statements" in line]
    assert len(deferral) == 1, report
    assert "this script" not in deferral[0]
    assert "this conversion" in deferral[0]


def test_a_cross_file_supersede_does_not_claim_the_two_share_a_file(tmp_path):
    """The same rule for the collision machinery underneath it: with the
    append read first and the rebuild second, the append is superseded -- and
    "a later statement in this file" names a file that isn't the one the
    superseded statement came from."""
    report = _report_dir(tmp_path, {"a_append.sql": APPEND_FILE, "b_rebuild.sql": REBUILD_FILE})
    superseded = [line for line in report.splitlines() if "superseded" in line]
    assert len(superseded) == 1, report
    assert "in this file" not in superseded[0]


def test_a_same_file_rebuild_deferral_still_reads_correctly(tmp_path):
    """The control: one file, and the wording still has to fit."""
    report = _report(tmp_path, REBUILD_FILE + APPEND_FILE)
    deferral = [line for line in report.splitlines() if "several statements" in line]
    assert len(deferral) == 1, report
    assert "orders_summary" in deferral[0]


# --- more than one statement writing one target, in any combination. The
# rebuild guard covered only the case where the FIRST statement was a full
# rebuild. A MERGE followed by an INSERT, an INSERT followed by a MERGE, and
# two plain INSERTs all lose one statement's whole operation the same way:
# `replace_draft`'s "later definition wins" is built for two competing
# definitions of a table, and none of these are that. Only a statement that
# defines the target's whole contents -- CREATE TABLE AS, or a TRUNCATE+INSERT
# pair -- genuinely replaces what came before it.

MERGE_STMT = (
    "MERGE INTO orders AS t USING raw.orders AS s ON t.order_id = s.order_id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n"
)
APPEND_STMT = "INSERT INTO orders SELECT order_id, status FROM raw.orders_backfill;\n"


def test_an_insert_after_a_merge_does_not_discard_the_merge(tmp_path):
    """The MERGE's ON-clause key, its match semantics and its whole source
    vanished, leaving an append model reading only the backfill."""
    report = _report(tmp_path, MERGE_STMT + APPEND_STMT)
    (body,) = [b for name, b in _models(tmp_path).items() if "orders" in name]
    assert "incremental_strategy='merge'" in body
    assert "unique_key='order_id'" in body
    assert "orders_backfill" not in body
    assert "several statements" in report
    assert "orders_backfill" in report


def test_a_merge_after_an_insert_does_not_discard_the_insert(tmp_path):
    """The mirror: reversed, the backfill was the half that disappeared."""
    report = _report(tmp_path, APPEND_STMT + MERGE_STMT)
    (body,) = [b for name, b in _models(tmp_path).items() if "orders" in name]
    assert "incremental_strategy='append'" in body
    assert "orders_backfill" in body
    assert "several statements" in report


def test_two_inserts_into_one_target_do_not_discard_the_first(tmp_path):
    """Two INSERTs both run and both land rows; calling the second a
    "redefinition" of the first is not what the SQL says, and the model built
    from the second alone drops everything the first inserted."""
    report = _report(
        tmp_path,
        "INSERT INTO rev SELECT id FROM raw.v1;\nINSERT INTO rev SELECT id FROM raw.v2;\n",
    )
    (body,) = [b for name, b in _models(tmp_path).items() if "rev" in name]
    assert "v1" in body
    assert "v2" not in body
    assert "several statements" in report
    assert "v2" in report


def test_a_later_full_rebuild_still_replaces_what_came_before_it(tmp_path):
    """The control, and the boundary: a CREATE TABLE AS does define the
    target's whole contents, so it genuinely replaces the earlier statement
    rather than adding to it."""
    report = _report(
        tmp_path,
        "INSERT INTO rev SELECT id FROM raw.v1;\nCREATE TABLE rev AS SELECT id FROM raw.v2;\n",
    )
    (body,) = [b for name, b in _models(tmp_path).items() if "rev" in name]
    assert "materialized='table'" in body
    assert "v2" in body
    assert "superseded" in report.lower()


# --- a column-list INSERT with no TRUNCATE to pair with. No pass converts
# one: truncate_insert_columns_pass wants a pair, and append_pass skips any
# INSERT carrying a column list. It was falling through both in silence --
# read, not converted, and not a word about it anywhere in the report.


def test_a_column_list_insert_with_no_truncate_is_not_silently_dropped(tmp_path):
    """The most direct "nothing is silent" violation the pipeline could
    produce: one statement in, no model, and "No decisions were recorded."""
    report = _report(
        tmp_path,
        "INSERT INTO orders (order_id, status) SELECT order_id, status FROM raw.backfill;\n",
    )
    assert "No decisions were recorded" not in report
    assert "no TRUNCATE" in report
    assert "orders" in report


def test_a_truncate_after_the_insert_is_not_a_pair_and_says_so(tmp_path):
    """A TRUNCATE that follows the INSERT wipes what it just wrote, so the two
    are not a rebuild pair. Same answer, same Decision owed."""
    report = _report(
        tmp_path,
        "INSERT INTO orders (order_id, status) SELECT order_id, status FROM raw.backfill;\n"
        "TRUNCATE TABLE orders;\n",
    )
    assert "no TRUNCATE" in report


def test_a_deferral_does_not_claim_a_model_that_was_never_built(tmp_path):
    """`written_earlier` answers "does an earlier statement write this
    target", which a still-pending statement does just as much as a converted
    one. The reason said "an earlier statement already became the model for
    this target" either way -- printed here in a report whose own summary
    says no models were produced at all."""
    report = _report(
        tmp_path,
        "INSERT INTO orders (order_id, status) SELECT order_id, status FROM raw.backfill;\n"
        "MERGE INTO orders AS t USING raw.orders AS s ON t.order_id = s.order_id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n",
    )
    assert "**Models**: 0" in report
    assert "already became the model" not in report
    assert "several statements" in report


def test_a_pending_merge_blocks_a_later_insert_the_same_way_a_pending_insert_does(tmp_path):
    """The target of a MERGE was not readable at all where pending statements
    are collected, so a MERGE never counted as writing anything -- a pending
    INSERT blocked a later MERGE, but never the reverse. The MERGE here is
    refused for its delete branch and stays pending; the INSERT after it must
    still see that something else writes this target."""
    report = _report(
        tmp_path,
        "MERGE INTO orders AS t USING raw.orders AS s ON t.order_id = s.order_id "
        "WHEN MATCHED THEN DELETE;\n"
        "INSERT INTO orders (order_id, status) SELECT order_id, status FROM raw.backfill;\n",
    )
    assert "several statements" in report
    assert "**Models**: 0" in report
