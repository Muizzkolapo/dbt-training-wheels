"""The report names each statement's file relative to the SQL the reader gave.

Testers found every Decision of a pasted or uploaded script citing
`(/var/folders/.../dbtw-source-xxxx/nightly.sql:9)`. That path is the
temporary directory where `dbtw web` stages a paste. It means nothing to the
reader, and delivery commits the report into the reader's project. For
`dbtw convert <dir>`, the path is relative to that directory. For one file,
it is the file name. The file is never lost: two files with one name in two
folders stay apart.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.emit.test_report import _change

from dbtw.cli.main import main
from dbtw.core.context import read_project
from dbtw.core.emit import emit
from dbtw.core.emit.report import render_report
from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import Decision

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"
STAGED = Path("/var/folders/xy/T/dbtw-source-abc123")


def _decision(source_file: str, line: int) -> Decision:
    return Decision(
        key="k1",
        tier=1,
        action="created model stg_orders",
        reason="because dbt",
        source_file=source_file,
        line_start=line,
        line_end=line,
    )


def _pending(source_file: str, line: int) -> tuple[tuple[int, ClassifiedStatement], ...]:
    raw = RawStatement(
        source_file=source_file, index=0, text="UPDATE t SET a = 1", line_start=line, line_end=line
    )
    return ((0, ClassifiedStatement(raw=raw, kind="update", reason="UPDATE mutates rows")),)


def test_a_staged_paste_is_named_by_its_file_name_only() -> None:
    staged = str(STAGED / "nightly.sql")
    report = render_report(
        _change(decisions=(_decision(staged, 9),), pending=_pending(staged, 12)),
        read_project(FIXTURES / "jaffle_shop"),
        sql_root=STAGED,
    )

    assert "(nightly.sql:9)" in report
    assert "`nightly.sql:12`" in report
    assert "/var/folders" not in report
    assert "dbtw-source" not in report


def test_a_directory_keeps_the_folder_under_it_so_two_files_stay_apart() -> None:
    root = Path("/home/priya/scripts")
    report = render_report(
        _change(
            decisions=(
                _decision(str(root / "daily" / "load.sql"), 3),
                _decision(str(root / "weekly" / "load.sql"), 5),
            ),
        ),
        read_project(FIXTURES / "jaffle_shop"),
        sql_root=root,
    )

    assert "(daily/load.sql:3)" in report
    assert "(weekly/load.sql:5)" in report
    assert "/home/priya" not in report


def test_emit_shows_a_staged_directory_by_file_name(tmp_path: Path) -> None:
    staged = tmp_path / "dbtw-source-xyz"
    staged.mkdir()
    (staged / "nightly.sql").write_text("SELECT 1", encoding="utf-8")
    out = tmp_path / "out"

    emit(
        _change(decisions=(_decision(str(staged / "nightly.sql"), 2),)),
        read_project(FIXTURES / "jaffle_shop"),
        out,
        sql_path=staged,
    )

    report = (out / "CONVERSION_REPORT.md").read_text(encoding="utf-8")
    assert "(nightly.sql:2)" in report
    assert str(tmp_path) not in report


def test_emit_shows_a_single_file_by_its_name(tmp_path: Path) -> None:
    sql = tmp_path / "deep" / "nightly.sql"
    sql.parent.mkdir()
    sql.write_text("SELECT 1", encoding="utf-8")
    out = tmp_path / "out"

    emit(
        _change(decisions=(_decision(str(sql), 2),)),
        read_project(FIXTURES / "jaffle_shop"),
        out,
        sql_path=sql,
    )

    report = (out / "CONVERSION_REPORT.md").read_text(encoding="utf-8")
    assert "(nightly.sql:2)" in report
    assert str(tmp_path) not in report


def test_the_cli_report_names_the_file_relative_to_the_sql_given(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    (scripts / "nightly").mkdir(parents=True)
    (scripts / "nightly" / "load.sql").write_text(
        "TRUNCATE TABLE revenue_daily;\n"
        "INSERT INTO revenue_daily SELECT order_id, amount FROM raw.orders;\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"

    code = main(
        [
            "convert",
            str(scripts),
            "--project",
            str(FIXTURES / "jaffle_shop"),
            "--out",
            str(out),
            "--no-remember",
        ]
    )

    assert code == 0
    report = (out / "CONVERSION_REPORT.md").read_text(encoding="utf-8")
    assert "(nightly/load.sql:2)" in report
    assert str(tmp_path) not in report
