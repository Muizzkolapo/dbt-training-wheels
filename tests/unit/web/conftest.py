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
is one, and it is testing that the session re-reads its inputs -- leaves the
next test's copy restored rather than whatever it left behind.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

PROJECTS = Path(__file__).parents[2] / "fixtures" / "projects"

# One append question and nothing else to answer.
ONE_APPEND = "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n"

# Two answerable Decisions from the two different families: a tier-2 pass key,
# which embeds the source path, and an assemble key, which does not.
VARIABLE_AND_APPEND = (
    "DECLARE @cutoff DATE = '2024-01-01';\n"
    "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders "
    "WHERE order_date >= @cutoff;\n"
)

# A MERGE names its key in its ON clause, so every option this question offers
# already spells that key and none of them carries a columns_prompt.
ONE_MERGE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *;\n"
)


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
