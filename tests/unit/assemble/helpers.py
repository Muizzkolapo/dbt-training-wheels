"""Shared fixtures for assemble tests that need a real ProjectContext or a
PassState carrying a script variable, mirrored from the variable tests in
test_assembler_rewrite.py rather than inventing a new shape.
"""

from __future__ import annotations

from pathlib import Path

from dbtw.core.context import ProjectContext, read_project
from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import ModelDraft, PassState

FIXTURES = Path(__file__).parents[2] / "fixtures"
PROJECTS = FIXTURES / "projects"


def context_for(project: str = "jaffle_shop") -> ProjectContext:
    return read_project(PROJECTS / project)


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
