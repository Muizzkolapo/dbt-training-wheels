"""What `dbtw convert` and `dbtw --help` print.

Testers on main found these defects:

1. The refusal to write inside the project named the project's own models
   as files "the conversion would replace". Those are not files this run
   writes. The refusal also started in lower case.
2. The "no dialect" warning printed before that refusal, where it is of no
   use to the reader.
3. The summary said "proposed 1 sources", "1 statements are still pending"
   and "the permissive default of sqlglot", and named no pending statement.
4. The help text had no description or example, did not say which values
   --dialect accepts, used terms that it did not define, and mixed lower
   and upper case at the start of the option help.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from dbtw.cli.main import _build_parser, main
from dbtw.core.context import read_project

FIXTURES = Path(__file__).parents[2] / "fixtures"
PROJECT = FIXTURES / "projects" / "jaffle_shop"


def _project_copy(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(PROJECT, project)
    return project


def _script(tmp_path: Path, text: str, name: str = "nightly.sql") -> Path:
    path = tmp_path / "sql" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_refusal_names_no_project_file_as_one_this_run_replaces(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project_copy(tmp_path)
    sql = _script(tmp_path, "INSERT INTO t SELECT a FROM raw.orders;\n")

    code = main(["convert", str(sql), "--project", str(project), "--out", str(project / "out")])

    err = capsys.readouterr().err
    assert code == 2
    for model in read_project(project).existing_models:
        assert model.path not in err, f"{model.path} is not a file this run writes"
    assert "already exist" not in err
    assert err[0].isupper(), err


def test_the_refusal_comes_before_any_warning_about_the_sql(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project_copy(tmp_path)
    sql = _script(tmp_path, "INSERT INTO t SELECT a FROM raw.orders;\n")

    main(["convert", str(sql), "--project", str(project), "--out", str(project / "out")])

    err = capsys.readouterr().err
    assert "warning:" not in err, err


def test_the_summary_uses_the_singular_for_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sql = _script(
        tmp_path,
        "INSERT INTO revenue_events SELECT order_id FROM raw.events;\n"
        "UPDATE revenue_events SET order_id = 1;\n",
    )

    code = main(
        [
            "convert",
            str(sql),
            "--project",
            str(PROJECT),
            "--out",
            str(tmp_path / "out"),
            "--no-remember",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "proposed 1 source." in out, out
    assert "1 statement is still pending" in out, out
    assert "1 sources" not in out and "1 statements" not in out


def test_the_summary_names_each_pending_statement_with_its_place_and_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sql = _script(
        tmp_path,
        "-- header\nINSERT INTO revenue_events SELECT order_id FROM raw.events;\n\n"
        "UPDATE revenue_events SET order_id = 1;\n",
    )

    main(
        [
            "convert",
            str(sql),
            "--project",
            str(PROJECT),
            "--out",
            str(tmp_path / "out"),
            "--no-remember",
        ]
    )

    out = capsys.readouterr().out
    (line,) = [row for row in out.splitlines() if "nightly.sql:4" in row]
    assert "UPDATE" in line or "update" in line, line
    assert str(tmp_path) not in line, "the place is relative to the SQL given"


def test_the_dialect_warning_uses_no_jargon(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sql = _script(tmp_path, "SELECT 1 AS a;\n")

    main(
        [
            "convert",
            str(sql),
            "--project",
            str(PROJECT),
            "--out",
            str(tmp_path / "out"),
            "--no-remember",
        ]
    )

    err = capsys.readouterr().err
    (warning,) = [row for row in err.splitlines() if row.startswith("warning:")]
    assert "sqlglot" not in warning
    assert "permissive" not in warning
    assert "--dialect" in warning


def _help(*argv: str, capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit):
        main([*argv, "--help"])
    return capsys.readouterr().out


def test_the_top_help_has_a_description_and_an_example(capsys: pytest.CaptureFixture[str]) -> None:
    text = _help(capsys=capsys)

    assert "dbt" in text.split("positional arguments")[0].split("usage:")[1]
    assert "Example" in text
    assert "dbtw convert " in text


def test_the_dialect_help_says_where_the_accepted_values_are(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for command in ("convert", "web"):
        text = " ".join(_help(command, capsys=capsys).split())
        options = text.split("options:")[1]
        dialect = options.split("--dialect DIALECT")[1].split(" --")[0]
        assert "postgres" in dialect, dialect
        assert "snowflake" in dialect, dialect


def test_the_help_uses_no_undefined_walk_terms(capsys: pytest.CaptureFixture[str]) -> None:
    top = " ".join(_help(capsys=capsys).split())
    web = " ".join(_help("web", capsys=capsys).split())
    convert = " ".join(_help("convert", capsys=capsys).split())

    for text in (top, web):
        assert "this conversion's questions" not in text
        assert "write action of the walk" not in text
    assert "append-or-merge question" not in convert


def test_every_help_string_starts_with_a_capital_letter() -> None:
    parser = _build_parser()
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps: list[str] = [str(a.help) for a in subparsers._choices_actions]
    for sub in subparsers.choices.values():
        helps += [str(a.help) for a in sub._actions if a.help and a.help != argparse.SUPPRESS]
    for text in helps:
        assert text[0].isupper(), text
