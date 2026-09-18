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

import pytest
from tests.unit.web.helpers import ONE_APPEND

from dbtw.web import state as state_module

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
