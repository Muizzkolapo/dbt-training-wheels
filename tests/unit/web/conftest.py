"""Fixtures for the web session tests: one dbt project and one SQL script,
both on disk, both where they will still be on the next run.

`sql_script` writes each script to a directory derived from the script's own
text rather than to a fresh `mkdtemp()` per call, for the reason
`tests/unit/assemble/helpers.convert` gives at length: a tier-2
`Decision.key` embeds the path of the file its statement was read from
(spec 11.7), so a script that moves between runs hands out keys that are
invalid on the next one and every answer held against them is refused. A
`Session` is a conversation over one path; a fixture that handed out a new
path per call would be exercising a session the product never builds.

Every call writes the file, so a test that edits a script in place -- there
are two, and they are testing that the session re-reads its inputs -- leaves
the next test's copy restored rather than whatever it left behind.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest
from tests.unit.web.helpers import ONE_APPEND

from dbtw.web import Session
from dbtw.web import state as state_module

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient

PROJECTS = Path(__file__).parents[2] / "fixtures" / "projects"


@pytest.fixture(scope="session")
def sql_script(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], Path]:
    root = tmp_path_factory.mktemp("dbtw-web-sql")

    def write(sql: str) -> Path:
        directory = root / hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
        directory.mkdir(exist_ok=True)
        path = directory / "in.sql"
        path.write_text(sql, encoding="utf-8")
        return path

    return write


@pytest.fixture
def sql_file(sql_script: Callable[[str], Path]) -> Path:
    return sql_script(ONE_APPEND)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    destination = tmp_path / "jaffle_shop"
    shutil.copytree(PROJECTS / "jaffle_shop", destination)
    return destination


@pytest.fixture
def pipeline_runs(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """One entry per full ingest -> classify -> passes -> assemble the session
    runs. `ingest` is the first call of `Session._run` and is made exactly
    once per run, so counting it counts conversions.
    """
    seen: list[Path] = []
    real = state_module.ingest

    def spy(source: Path, dialect: str | None = None):  # type: ignore[no-untyped-def]
        seen.append(source)
        return real(source, dialect)

    monkeypatch.setattr(state_module, "ingest", spy)
    return seen


SQL = Path(__file__).parents[2] / "fixtures" / "sql"


@pytest.fixture
def walk_sql() -> Path:
    """The script the ten walkthroughs were run against (spec section 11).

    The checked-in fixture itself, read and never written: a `Session` needs
    one path that does not move (spec 11.7), and this one is as stable as a
    path gets. The project beside it is always a copy -- `project_dir` -- so
    nothing this walk does can reach the repository's own test data.
    """
    return SQL / "incremental_etl.sql"


class Walk(Protocol):
    """Build the walk over one script: (app, client, session)."""

    def __call__(
        self, sql: Path, dialect: str | None = None
    ) -> tuple[Flask, FlaskClient, Session]: ...


@pytest.fixture
def walk(project_dir: Path) -> Walk:
    """The app, a client for it, and the session all three share.

    `create_app` is imported inside the factory rather than at module scope:
    `dbtw.web` is documented to stay importable without Flask, and a conftest
    that imported the app at the top would make every session test in this
    directory need the extra too.
    """

    def build(sql: Path, dialect: str | None = None) -> tuple[Flask, FlaskClient, Session]:
        from dbtw.web.app import create_app

        session = Session(project=project_dir, sql=sql, dialect=dialect)
        app = create_app(session)
        app.testing = True
        return app, app.test_client(), session

    return build
