"""Every string this conversion's engine produced, gathered independently of
the web layer.

`test_no_screen_authors_a_sentence` checks each `data-engine` claim against
this set by exact match, so the set is built from `dbtw.core` alone -- from
the session's own conversion, the glossary, the worked examples, and the
files `emit` would write. Nothing here reads a template or a route. If the
walk shows a string the engine never produced, it is not in here, and the
claim fails.

The files are gathered by running `emit` into a temporary directory and
reading back what it wrote, rather than by re-deriving the paths: emit
decides where a sources file and a per-model schema .yml land, and a second
copy of that reasoning in a test would be free to agree with a screen while
both disagreed with what is written.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

from tests.unit.web.page import normalised

from dbtw.core.assemble import ProjectChange
from dbtw.core.context import read_project
from dbtw.core.emit import (
    AFTER_RUN_LABEL,
    PLACEHOLDER_NOTICE,
    SUPPOSED_ROW_LABEL,
    emit,
    worked_example,
)
from dbtw.core.teach import GLOSSARY
from dbtw.web import Session


def _from_change(change: ProjectChange) -> Iterator[str]:
    yield change.project_name
    if change.dialect:
        yield change.dialect

    for decision in change.decisions:
        yield from (
            decision.key,
            decision.action,
            decision.reason,
            decision.plain_reason,
            decision.question,
            decision.plain_question,
            decision.chosen,
            str(decision.tier),
            decision.source_file,
        )
        if decision.subject is not None:
            yield decision.subject.table
            yield from decision.subject.columns
            yield from decision.subject.candidates
        for option in decision.options:
            yield from (
                option.label,
                option.effect,
                option.plain,
                option.columns_prompt,
                option.kind,
                option.declares_test,
            )

    for model in change.models:
        yield from (model.name, model.path, model.body, model.layer)
        if model.materialization:
            yield model.materialization
        yield from model.depends_on
        yield from model.leading_comments
        if model.incremental_strategy:
            yield model.incremental_strategy
        yield from model.unique_key

    for source in change.sources:
        yield from (source.source_name, source.schema, source.table)

    for test in change.tests:
        yield from (test.model, test.column, test.test)

    for _, statement in change.pending:
        yield from (statement.kind, statement.reason, statement.raw.text, statement.raw.source_file)

    for variable in change.variables:
        yield variable.name
        if variable.default_sql is not None:
            yield variable.default_sql


def _from_examples(change: ProjectChange) -> Iterator[str]:
    yield from (PLACEHOLDER_NOTICE, SUPPOSED_ROW_LABEL, AFTER_RUN_LABEL)
    for decision in change.decisions:
        for model in change.models:
            example = worked_example(decision, model, change.dialect)
            if example is None:
                continue
            yield example.key
            yield from example.columns
            for row in (*example.before, *example.model_after):
                yield from row


def written_files(session: Session, change: ProjectChange | None = None) -> dict[str, str]:
    """Every file this conversion would write, by project-relative path.

    Run through `emit` into a directory that is thrown away, rather than
    re-derived: emit decides where a sources file and a per-model schema .yml
    land, and a second copy of that reasoning in a test would be free to
    agree with a screen while both disagreed with what is written.
    """
    change = change if change is not None else session.view().change
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "preview"
        result = emit(change, read_project(session.project), out)
        return {
            path.relative_to(out).as_posix(): path.read_text(encoding="utf-8")
            for path in result.paths
        }


def _from_files(session: Session, change: ProjectChange) -> Iterator[str]:
    for path, contents in written_files(session, change).items():
        yield path
        yield contents


def engine_strings(session: Session) -> frozenset[str]:
    """Every string `dbtw.core` produces for this conversation, normalised."""
    change = session.view().change
    produced: list[str] = [str(session.sql), str(session.project)]
    produced.extend(_from_change(change))
    produced.extend(_from_examples(change))
    produced.extend(_from_files(session, change))
    for term in GLOSSARY:
        produced.append(term.name)
        produced.append(term.plain)
        produced.extend(term.seen_as)
    return frozenset(normalised(text) for text in produced if text.strip())
