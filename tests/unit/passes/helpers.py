"""Running the real pass pipeline over a snippet, for the pass tests."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from dbtw.core.ingest import classify_statements, ingest
from dbtw.core.passes import PassState, run_passes

_ROOT = Path(tempfile.gettempdir()) / "dbtw-pass-helpers"


def state_from(sql: str, *, filename: str = "in.sql", dialect: str | None = None) -> PassState:
    """The pass pipeline's output for `sql`, written to a file called `filename`.

    The filename is the point for `select_pass`: dbt names a model after its
    file, so a test about that rule has to control the file's name. Written
    under a directory derived from the SQL so the path is stable between
    runs -- a tier-2 `Decision.key` embeds it (spec 11.7).
    """
    directory = _ROOT / hashlib.sha256((sql + filename).encode()).hexdigest()[:16]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(sql, encoding="utf-8")
    result = ingest(path, dialect)
    return run_passes(classify_statements(result), result.dialect)


def state_from_files(files: dict[str, str], *, dialect: str | None = None) -> PassState:
    """The pass pipeline's output for a whole directory of files.

    `files` maps a path relative to the ingested directory onto its SQL, so a
    test can put two files of the same stem in two folders -- which is the one
    way two file-named models collide that a single-file helper cannot build.
    """
    digest = hashlib.sha256(repr(sorted(files.items())).encode()).hexdigest()[:16]
    directory = _ROOT / digest
    for relative, sql in sorted(files.items()):
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sql, encoding="utf-8")
    result = ingest(directory, dialect)
    return run_passes(classify_statements(result), result.dialect)
