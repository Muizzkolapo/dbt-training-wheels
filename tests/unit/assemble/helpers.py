"""Shared fixtures for assemble tests that need a real ProjectContext or a
PassState carrying a script variable, mirrored from the variable tests in
test_assembler_rewrite.py rather than inventing a new shape.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from dbtw.core.assemble import ProjectChange, assemble
from dbtw.core.context import ProjectContext, read_project
from dbtw.core.ingest import ClassifiedStatement, RawStatement, classify_statements, ingest
from dbtw.core.passes import Answer, ModelDraft, PassState, run_passes

FIXTURES = Path(__file__).parents[2] / "fixtures"
PROJECTS = FIXTURES / "projects"

# One directory per test session, with one subdirectory per distinct SQL
# script inside it -- see convert() for why the path has to be stable.
_CONVERT_ROOT = Path(tempfile.mkdtemp(prefix="dbtw-convert-"))


def context_for(project: str = "jaffle_shop") -> ProjectContext:
    return read_project(PROJECTS / project)


def convert(
    sql: str,
    *,
    answers: Mapping[str, Answer] | None = None,
    unique_key: tuple[str, ...] = (),
    project: str = "jaffle_shop",
    dialect: str | None = None,
    descriptions: Mapping[str, str] | None = None,
    elsewhere: Sequence[ProjectContext] = (),
    tags: Mapping[str, Sequence[str]] | None = None,
    materializations: Mapping[str, str] | None = None,
) -> ProjectChange:
    """Run the real ingest -> classify -> passes -> assemble pipeline over `sql`.

    The same path the CLI takes, so a test that answers a question here is
    answering the question a user would actually be shown.

    The file is written to a directory derived from the SQL itself rather than
    to a fresh `mkdtemp()` per call. A tier-2 `Decision.key` embeds its source
    file's path ("tier2.append.<path>:<index>"), so the whole point of an
    answer -- naming a key an earlier run handed out -- only works if two
    convert() calls on the same script agree on where that script lives. A
    fresh directory per call would make every key single-use, and every answer
    keyed from a previous call would be refused as unknown. The dialect is part
    of that identity too: the same text read as two dialects is two different
    conversions.
    """
    keyed_on = f"{dialect}\n{sql}"
    directory = _CONVERT_ROOT / hashlib.sha256(keyed_on.encode("utf-8")).hexdigest()[:16]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "in.sql"
    path.write_text(sql, encoding="utf-8")

    result = ingest(path, dialect)
    state = run_passes(classify_statements(result), result.dialect)
    return assemble(
        state,
        context_for(project),
        unique_key=unique_key,
        answers=answers,
        descriptions=descriptions,
        elsewhere=elsewhere,
        tags=tags,
        materializations=materializations,
    )


def state_with_variable() -> PassState:
    raw = RawStatement(
        source_file="e.sql",
        index=0,
        text="DECLARE @cutoff DATE = '2024-01-01'",
        line_start=1,
        line_end=1,
    )
    stmt = ClassifiedStatement(raw=raw, kind="variable", reason="t")
    draft = ModelDraft(
        name="m",
        qualified_name="m",
        identity=("", "", "m"),
        body="SELECT a FROM raw.t WHERE d >= @cutoff",
        materialization="table",
        grants=(),
        source_indices=(1,),
        leading_comments=(),
    )
    return PassState(pending=((0, stmt),), drafts=(draft,), decisions=(), dialect="tsql")
