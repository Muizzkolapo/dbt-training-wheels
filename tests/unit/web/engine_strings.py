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

The stale states are the third and fourth: while an answer is held whose
question the current SQL no longer asks -- or a description of a model it no
longer builds -- the answered run cannot be computed at all. Then the set is
the pristine run plus what the screen that gets the reader out renders, which
is the keys `stale_answers` names or the models `stale_descriptions` does.

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

from dbtw.core.assemble import ProjectChange, UnknownAnswerError, UnknownModelError
from dbtw.core.context import read_project
from dbtw.core.emit import (
    AFTER_RUN_LABEL,
    PLACEHOLDER_NOTICE,
    SUPPOSED_ROW_LABEL,
    emit,
    worked_example,
)
from dbtw.core.intro import HEADLINE, LEDE, PILLARS, PRIVACY
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

    # The reader's own words, carried back out of the engine. They are
    # rendered on the describe screen and inside the .yml the files screen
    # shows, and both claim them as engine runs -- which they are, in the one
    # sense the claim is about: the text on screen is exactly what
    # `change.descriptions` holds, so a template that altered a word of it
    # fails the same exact-match check a Decision's reason does.
    for described in change.descriptions:
        yield from (described.model, described.text)

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
    except (UnknownAnswerError, UnknownModelError):
        # Something the reader holds that the current SQL no longer has a
        # place for: an answer to a question it no longer asks, or a
        # description of a model it no longer builds. Nothing but the pristine
        # run can be computed, and the screen that gets them out renders the
        # keys or the model names.
        produced.extend(session.stale_answers())
        produced.extend(session.answers)
        if not session.stale_answers():
            # Only askable once the answers resolve -- see
            # `Session.stale_descriptions`, which raises while they do not.
            produced.extend(session.stale_descriptions())
            produced.extend(session.descriptions)
    else:
        produced.extend(_from_change(change))
        produced.extend(_from_examples(change))
        produced.extend(_from_files(session, change))

    # The reader's own SQL, statement by statement, which the "what changed"
    # screen renders beside each model. Produced by `ingest` rather than by a
    # conversion -- it is the input -- and every word of it is on that screen
    # claimed as the engine's, so it belongs in the set that claim is checked
    # against.
    produced.extend(statement.raw.text for statement in session.originals())
    # What this costs, stated rather than discovered later: `produced` is one
    # set checked against every page, so after this any template on any
    # screen could wrap a whole statement of the reader's file in
    # `data-engine` and pass. It is not avoidable while the what-changed
    # screen renders their SQL -- unmarked, the same text is authored prose
    # and fails the threshold instead -- so the check here is weaker by
    # exactly the width of the reader's own file.

    # What the tool says about itself on the screen a reader meets first.
    # Engine-owned for the reason `dbtw.core.intro` gives -- a screen may not
    # write a sentence -- so it belongs in the set every marked string on
    # every screen is checked against.
    produced.extend((HEADLINE, LEDE, PRIVACY))
    for pillar in PILLARS:
        produced.extend((pillar.number, pillar.name, pillar.plain))

    for term in GLOSSARY:
        produced.append(term.name)
        produced.append(term.plain)
        produced.extend(term.seen_as)
    return frozenset(normalised(text) for text in produced if text.strip())
