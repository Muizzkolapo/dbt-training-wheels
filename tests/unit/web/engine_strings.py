"""Every string this conversion's engine produced, gathered independently of
the web layer.

`test_no_screen_authors_a_sentence` checks each `data-engine` claim against
this set by exact match, so the set is built from `dbtw.core` alone -- from
the session's own conversion, the glossary, the worked examples, and the
files `emit` would write. Nothing here reads a template or a route. If the
walk shows a string the engine never produced, it is not in here, and the
claim fails.

**Two runs, because the screens read two.** A question screen renders the
options of the *answered* run and the column requirement of the *pristine*
one -- that split is the design, and it is the whole reason `SessionView`
carries `prompts` separately. Gathered from the answered run alone, the set
is missing every pristine `columns_prompt` (they are `""` once an answer is
applied), so the honest page fails the claim and the only state in which the
claim holds is the one with no answers. That made the strongest assertion on
the branch true by construction, which is the shape of guard blind review
keeps finding here.

The stale state is the third: while an answer is held whose question the
current SQL no longer asks, the answered run cannot be computed at all. Then
the set is the pristine run plus the keys `stale_answers` names, which is
exactly what that screen renders.

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

from dbtw.core.assemble import ProjectChange, UnknownAnswerError
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


def _pristine(session: Session) -> Session:
    """The same conversation with no answers in it.

    Built through the public constructor rather than reached for inside the
    session: the pristine run is a conversion of the same two paths, and a
    test that needed a private accessor to name what a screen reads would be
    reading the implementation instead of the contract.
    """
    return Session(project=session.project, sql=session.sql, dialect=session.dialect)


def engine_strings(session: Session, out: Path) -> frozenset[str]:
    """Every string `dbtw.core` produces for this conversation, normalised.

    Both runs the screens read, and the files, and the glossary.

    Plus the conversation's own three paths. The SQL, the project and the
    destination are not `dbtw.core`'s output -- they are what the user handed
    the command, and each is rendered verbatim on a screen. The claim this
    set is used to check is that no *template* authors text, and a path the
    caller supplied is not a template authoring anything. `out` is required
    rather than defaulted for the same reason `ProjectContext.root` is: the
    two write screens are checked in every state, and a destination a caller
    could forget to pass would make the check pass by having nothing to say.
    """
    produced: list[str] = [str(session.sql), str(session.project), str(out)]

    pristine = _pristine(session).view().change
    produced.extend(_from_change(pristine))
    produced.extend(_from_examples(pristine))

    try:
        change = session.view().change
    except UnknownAnswerError:
        # A held answer the current SQL no longer asks for. Nothing but the
        # pristine run can be computed, and the stale screen renders the keys.
        produced.extend(session.stale_answers())
        produced.extend(session.answers)
    else:
        produced.extend(_from_change(change))
        produced.extend(_from_examples(change))
        produced.extend(_from_files(session, change))

    for term in GLOSSARY:
        produced.append(term.name)
        produced.append(term.plain)
        produced.extend(term.seen_as)
    return frozenset(normalised(text) for text in produced if text.strip())
