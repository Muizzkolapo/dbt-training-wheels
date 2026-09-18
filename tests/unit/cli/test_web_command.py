"""`dbtw web` — the two refusals that happen before anything is served.

Spec 7 puts "the project path is not a dbt project" on the command line
rather than on a screen, and the web extra is the same shape of problem: the
user's command cannot work as given, which is what `_USAGE_ERRORS` is for.

Every test converts against a *copy* of the fixture project rather than the
fixture itself. This command writes nothing, and one of these tests is what
says so — run against the shared fixture, a regression that made it write
would leave the conversion inside the repository's own test data, where the
next test to read it would find a project it did not describe.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from dbtw.cli.main import main

FIXTURE = Path(__file__).parents[2] / "fixtures" / "projects" / "jaffle_shop"

ONE_APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    destination = tmp_path / "jaffle_shop"
    shutil.copytree(FIXTURE, destination)
    return destination


def _sql(tmp_path: Path, text: str = ONE_APPEND) -> Path:
    path = tmp_path / "in.sql"
    path.write_text(text, encoding="utf-8")
    return path


def _no_flask(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `import flask` fail the way an uninstalled extra makes it fail.

    A None in sys.modules is the documented way to block an import; it raises
    ModuleNotFoundError, which is what a genuinely absent package raises too.
    A Flask that is present but cannot be imported raises the wider
    ImportError instead, and has its own test below.
    """
    monkeypatch.setitem(sys.modules, "flask", None)


def test_web_reports_the_questions_the_walk_will_ask(
    tmp_path: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["web", str(_sql(tmp_path)), "--project", str(project)]) == 0
    out = capsys.readouterr().out
    assert "1 question to answer." in out
    assert "jaffle_shop" in out


def test_web_counts_every_question_the_session_carries(
    tmp_path: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two questions, counted — a hard-coded "1 question" would pass the test
    above and fail this one.
    """
    sql = _sql(
        tmp_path,
        "DECLARE @cutoff DATE = '2024-01-01';\n"
        "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders "
        "WHERE order_date >= @cutoff;\n",
    )
    assert main(["web", str(sql), "--project", str(project), "--dialect", "tsql"]) == 0
    assert "2 questions to answer." in capsys.readouterr().out


def test_web_refuses_a_project_that_is_not_a_dbt_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    not_a_project = tmp_path / "elsewhere"
    not_a_project.mkdir()
    assert main(["web", str(_sql(tmp_path)), "--project", str(not_a_project)]) == 2
    err = capsys.readouterr().err
    assert "dbt_project.yml" in err
    assert str(not_a_project) in err


def test_web_refuses_without_the_extra_and_names_it(
    tmp_path: Path,
    project: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_flask(monkeypatch)
    assert main(["web", str(_sql(tmp_path)), "--project", str(project)]) == 2
    err = capsys.readouterr().err
    assert "pip install 'dbt-training-wheels[web]'" in err
    assert "Flask" in err


def test_a_flask_that_cannot_be_imported_is_refused_the_same_way(
    tmp_path: Path,
    project: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed but broken — a Flask whose own imports fail raises
    ImportError rather than ModuleNotFoundError. The command cannot serve
    either way, so it refuses either way, and the original error travels in
    the message so a broken install is not reported as a missing one.
    """
    packages = tmp_path / "packages"
    packages.mkdir()
    (packages / "flask.py").write_text(
        "raise ImportError('cannot import name Markup from werkzeug')\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(packages))
    monkeypatch.delitem(sys.modules, "flask", raising=False)

    assert main(["web", str(_sql(tmp_path)), "--project", str(project)]) == 2
    err = capsys.readouterr().err
    assert "pip install 'dbt-training-wheels[web]'" in err
    assert "cannot import name Markup from werkzeug" in err


def test_the_missing_extra_is_refused_before_the_project_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both wrong at once. The extra is what the user must fix first — no
    argument of theirs can make the command work without it — so that is the
    refusal they get, rather than one about a project the command could not
    have served anyway.
    """
    _no_flask(monkeypatch)
    not_a_project = tmp_path / "elsewhere"
    not_a_project.mkdir()
    assert main(["web", str(_sql(tmp_path)), "--project", str(not_a_project)]) == 2
    err = capsys.readouterr().err
    assert "pip install 'dbt-training-wheels[web]'" in err
    assert "dbt_project.yml" not in err


def test_the_project_is_read_before_the_sql_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both arguments wrong at once. The project is a precondition of the
    whole command (spec 7), so it is what the refusal is about — a user told
    their SQL is missing would fix it and be refused again.
    """
    not_a_project = tmp_path / "elsewhere"
    not_a_project.mkdir()
    missing = tmp_path / "nothing.sql"
    assert main(["web", str(missing), "--project", str(not_a_project)]) == 2
    err = capsys.readouterr().err
    assert "dbt_project.yml" in err
    assert str(missing) not in err


def test_web_refuses_a_missing_sql_file(
    tmp_path: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nothing.sql"
    assert main(["web", str(missing), "--project", str(project)]) == 2
    assert str(missing) in capsys.readouterr().err


def test_web_refuses_an_unknown_dialect(
    tmp_path: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["web", str(_sql(tmp_path)), "--project", str(project), "--dialect", "sqlserver"])
    assert code == 2
    assert "sqlserver" in capsys.readouterr().err


def test_web_expands_a_home_relative_project_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """--project is expanded before it is read, as it is for convert. Without
    that, '~/proj' is looked for in a directory literally named '~'.
    """
    home = tmp_path / "home"
    shutil.copytree(FIXTURE, home / "proj")
    monkeypatch.setenv("HOME", str(home))

    assert main(["web", str(_sql(tmp_path)), "--project", "~/proj"]) == 0
    assert "jaffle_shop" in capsys.readouterr().out


def test_web_writes_nothing(tmp_path: Path, project: Path) -> None:
    """The command prepares a conversation; writing is a separate action a
    user takes. Nothing lands on disk, least of all inside the project.
    """
    before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))

    assert main(["web", str(_sql(tmp_path)), "--project", str(project)]) == 0

    assert sorted(p.relative_to(project).as_posix() for p in project.rglob("*")) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.sql", "jaffle_shop"]
