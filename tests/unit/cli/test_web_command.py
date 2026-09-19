"""`dbtw web` — the refusals that happen before anything is served, and what
reaches the server when nothing refuses.

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
from dataclasses import dataclass
from pathlib import Path

import pytest

from dbtw.cli.main import main


@dataclass
class Served:
    """What `dbtw web` handed the server, instead of running one.

    `dbtw.web.app.serve` blocks until the user stops it, so every test here
    replaces it. The replacement is installed by an autouse fixture rather
    than per test: a test that forgot it would not fail, it would hang, and a
    suite that hangs says nothing about what it was checking.
    """

    calls: list[tuple[str, int, bool]]

    @property
    def once(self) -> tuple[str, int, bool]:
        (call,) = self.calls
        return call


@pytest.fixture(autouse=True)
def served(monkeypatch: pytest.MonkeyPatch) -> Served:
    record = Served(calls=[])

    def spy(app: object, host: str, port: int, open_browser: bool) -> None:
        assert app is not None
        record.calls.append((host, port, open_browser))

    monkeypatch.setattr("dbtw.web.app.serve", spy)
    return record


FIXTURE = Path(__file__).parents[2] / "fixtures" / "projects" / "jaffle_shop"

ONE_APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A copy of the fixture project in a directory *not* named after it.

    The declared name in `dbt_project.yml` is `jaffle_shop`; the directory is
    not. Copied to `<tmp>/jaffle_shop`, a test asserting the reported name
    cannot tell it from the path or from the directory's basename, because all
    three read the same.
    """
    destination = tmp_path / "checkout"
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
    sql = _sql(tmp_path)
    assert main(["web", str(sql), "--project", str(project)]) == 0
    # The whole line. "jaffle_shop" *in* the output cannot tell the project's
    # name from its path, because the copy lives at <tmp>/jaffle_shop -- a
    # print of the path would satisfy it.
    assert capsys.readouterr().out == f"{sql} \u2192 jaffle_shop: 1 question to answer.\n"


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


def test_web_serves_the_walk_on_the_default_port_and_opens_a_browser(
    tmp_path: Path, project: Path, served: Served
) -> None:
    assert main(["web", str(_sql(tmp_path)), "--project", str(project)]) == 0
    assert served.once == ("127.0.0.1", 5000, True)


def test_web_serves_on_the_port_it_was_given(tmp_path: Path, project: Path, served: Served) -> None:
    assert main(["web", str(_sql(tmp_path)), "--project", str(project), "--port", "8123"]) == 0
    assert served.once == ("127.0.0.1", 8123, True)


def test_no_browser_leaves_the_browser_alone(tmp_path: Path, project: Path, served: Served) -> None:
    assert main(["web", str(_sql(tmp_path)), "--project", str(project), "--no-browser"]) == 0
    assert served.once == ("127.0.0.1", 5000, False)


def test_a_port_that_is_not_a_number_is_refused_by_the_parser(
    tmp_path: Path, project: Path, served: Served
) -> None:
    """argparse's own refusal, and it exits rather than serving. A --port
    accepted as a string would reach `make_server` and fail there, after the
    conversion had been read.
    """
    with pytest.raises(SystemExit, match="2"):
        main(["web", str(_sql(tmp_path)), "--project", str(project), "--port", "http"])
    assert served.calls == []


def test_nothing_is_served_when_the_command_refuses(
    tmp_path: Path, served: Served, capsys: pytest.CaptureFixture[str]
) -> None:
    not_a_project = tmp_path / "elsewhere"
    not_a_project.mkdir()
    assert main(["web", str(_sql(tmp_path)), "--project", str(not_a_project)]) == 2
    assert served.calls == []
    assert "dbt_project.yml" in capsys.readouterr().err


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

    sql = _sql(tmp_path)
    assert main(["web", str(sql), "--project", "~/proj"]) == 0
    assert capsys.readouterr().out == f"{sql} \u2192 jaffle_shop: 1 question to answer.\n"


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Every path under `root` and, for files, what is in it.

    Contents and not just names: a conversion writing over a model the project
    already has adds no path, and that is the write this command must not make
    most of all.
    """
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def test_web_refuses_an_out_dir_inside_the_project_before_serving_anything(
    tmp_path: Path, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`dbtw convert` refuses this before it writes a byte; `dbtw web`
    must refuse it before it serves a page, for the same reason. Discovering
    it only when the write button is pressed means a person who has never
    used dbt walked six screens on the strength of a conversation that was
    never going anywhere -- the refusal `emit()` already carries, arriving
    three actions later than it has to.
    """
    inside = project / "out"
    assert main(["web", str(_sql(tmp_path)), "--project", str(project), "--out", str(inside)]) == 2
    err = capsys.readouterr().err
    assert "refusing" in err
    assert not inside.exists()


def test_web_writes_nothing(tmp_path: Path, project: Path) -> None:
    """Starting the walk prepares a conversation; writing is a separate
    action a user takes, and this build does not serve the route that takes
    it. Nothing lands on disk, least of all inside the project.
    """
    before = _snapshot(project)

    assert main(["web", str(_sql(tmp_path)), "--project", str(project)]) == 0

    assert _snapshot(project) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["checkout", "in.sql"]
