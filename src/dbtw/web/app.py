"""The guided walk: seven screens over one conversion, and the loop that
answers it.

The templates render engine strings and structure, and nothing else. Every
explanatory sentence on every screen is a `Decision.reason`, a
`Decision.plain_reason`, an `Option.effect`, an `Option.plain`, an `Example`,
or a `teach` glossary entry (spec section 6.1). Each one is wrapped in an
element carrying `data-engine`, and `test_no_screen_authors_a_sentence`
checks every such claim by exact match against what `dbtw.core` produced --
so the marker is an assertion this module makes and a test disproves, not a
label a template can hide behind. If a screen needs an explanation nothing
carries, that is a core change, not a template one.

What each route gathers, it gathers from `Session.view()`: one pristine
pipeline run plus, once an answer is held, one answered run, whatever the
number of questions. The accessors are there for a caller that wants one
thing; a screen wants three, and asking for them separately costs a
conversion each.

Two screens ask for one run more, and both buy something with it. A question
screen runs the conversion *without* its own answer, so it can say what that
answer -- and no other -- put on the record; the caveats screen runs it with
no answers at all, so it can hand those records to the question screens
instead of keeping them. Both are the same pipeline on the same inputs with a
different answer set, which is the only way to ask what one answer is
responsible for, and neither runs at all before the first answer is given. At
spec 4.3's measured 17 ms for eight statements that is one screen at four
runs rather than two.

Two things this module deliberately does not do:

* it does not read a rendered option to find out whether an answer needs
  columns. `SessionView.prompts` carries the requirement off the *pristine*
  question, and after the first answer the rebuilt options carry no
  `columns_prompt` at all while the answer still needs one. A picker built
  from the screen would offer no way to name a second key, and "append" would
  be the only answer left;
* it does not decide where the write goes, and it does not guard where the
  write goes. `dbtw web --out` names the destination and `create_app` is
  handed it; the refusal that keeps a conversion out of the user's own dbt
  project lives inside `emit`, so `/write` inherits it rather than
  remembering it. A second copy of that rule here is the drift this project
  deletes fields to avoid, and the rule is about writing rather than about
  one front end.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import webbrowser
from collections.abc import Container, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from flask import Flask, abort, g, redirect, render_template, request, url_for
from markupsafe import Markup, escape
from werkzeug.wrappers.response import Response

from dbtw.core.assemble import (
    CHOOSABLE,
    MATERIALIZATION_PLAIN,
    AssembledModel,
    ProjectChange,
    UnknownAnswerError,
    UnknownModelError,
)
from dbtw.core.context import NotADbtProjectError, read_project
from dbtw.core.deliver import (
    BranchExistsError,
    Delivery,
    DirtyWorkingTreeError,
    GitFailedError,
    NoRemoteError,
    NotAGitRepoError,
    deliver,
    earlier_write,
    free_branch,
    push,
)
from dbtw.core.emit import (
    AFTER_RUN_LABEL,
    PLACEHOLDER_NOTICE,
    REPORT_NAME,
    SUPPOSED_ROW_LABEL,
    Example,
    OutputInsideProjectError,
    StaleOutputError,
    UnsafeOutputPathError,
    emit,
    render_model,
    worked_example,
)
from dbtw.core.ingest.types import ClassifiedStatement
from dbtw.core.intro import HEADLINE, LEDE, PILLARS, PRIVACY
from dbtw.core.passes import Decision, Option
from dbtw.core.progress import (
    NO_GLOSSARY,
    NOTHING_EXPLAINED,
    Progress,
    after,
    load_progress,
    save_progress,
    teaching_for,
)
from dbtw.core.teach import COMMANDS, Term, terms_in
from dbtw.web.state import (
    EmptySourceError,
    ProjectPathError,
    Session,
    SessionView,
    Source,
    UnnamedPasteError,
    pasted_file_name,
)

# The key prefix `assemble` gives the Decisions it records for a model rather
# than for a statement -- `assemble.rename.<name>`, the shape
# `passes.types.statement_index` documents when it explains which keys carry
# no trailing statement index. The cutover is what these Decisions are about,
# and two of five personas asked for it on the first screen rather than at the
# end, so the first screen is where they render. Everything else that asks
# nothing renders on the caveats screen: between them the two screens cover
# every Decision that is not a question, and
# `test_every_decision_reaches_exactly_one_screen` is what holds that.
_RENAME = "assemble.rename."

# The commands the last screen names. Four command *names*, each one a
# `teach.Term.name` verbatim -- `test_the_last_screen_names_the_commands_the
# _glossary_defines` holds them against the glossary, so this list cannot
# name a command nothing defines. What each one does is the glossary's to
# say, and it is said once, in the block every screen builds with `terms_in`.
#
# No flags. `dbt build --select stg_events+` was a hard stop for four of five
# personas -- the trailing `+` especially -- and nothing in the engine knows
# which models a reader should select, so a `--select` written here would be
# the web layer inventing behaviour.
_COMMANDS = COMMANDS


@dataclass(frozen=True, slots=True)
class Screen:
    """One page of the walk, as the nav names it.

    `engine` says whether `label` is a string the engine produced -- a
    question screen is named by the table its question is about -- so the
    template knows whether to mark it.

    `state` is the dot the design puts beside every step, and it is derived
    from the conversion rather than from where the reader has got to: "ask"
    for a question nobody has answered, "ok" for one that has been answered
    or a screen whose work is done, "stands" for a screen with nothing to
    decide. The design's own note on that rail is "position in the flow never
    marks a step done", and deriving every dot is how this keeps it -- a
    reader who walks past a question without answering it is still shown a
    question wanting an answer.
    """

    url: str
    label: str
    engine: bool
    state: str = "stands"
    # The kind of question a question screen asks, read off the documented
    # prefix of its `Decision.key` ("tier2.append.", "assemble.layer.").
    # Shown beside the label because the label alone is the table, and two
    # questions about one model -- its append question and its layer
    # question -- were two steps with one name. An identifier, not prose:
    # each one is the name of the pass that asks, and none is a dbt word the
    # glossary defines.
    kind: str = ""
    # A number beside the label when another question step has the same
    # table and kind, so two steps never read the same. Empty otherwise.
    nth: str = ""


@dataclass(frozen=True, slots=True)
class Column:
    """One entry in a column picker.

    `tick` is False for the second and later rows carrying a name already
    offered. `Subject.candidates` is deliberately not deduplicated by the
    engine, because `SELECT o.amount, p.amount` really does project two
    columns of one name and the ambiguity is the user's to see -- so every
    projection is listed. Only the first carries the input: an answer is a
    column *name*, so two ticks would send the same string, and offering a
    choice between two identical answers is offering a choice that is not
    one.
    """

    name: str
    tick: bool
    # Ticked already, because it is in the answer the reader gave with this
    # option. Without it the picker came back empty under a button that
    # names the key -- "merge on event_id" -- and pressing that button sent
    # no columns and was refused.
    checked: bool


@dataclass(frozen=True, slots=True)
class Choice:
    """One answer on screen: the option, whether it stands, and its picker.

    `prompt` is read off `SessionView.prompts` -- the pristine question --
    and never off `option.columns_prompt`. See the module docstring.
    """

    option: Option
    chosen: bool
    prompt: str
    columns: tuple[Column, ...]
    # Whether the reader has answered this question. The engine proposes an
    # option on every question, so `chosen` alone cannot tell "the reader
    # took this" from "the SQL suggests this" -- and marking the proposal
    # "Chosen" contradicted the rail's dot, which says the question still
    # wants an answer.
    answered: bool
    # The part of the reader's own answer the picker does not list -- a key
    # they typed that this model's projections do not name -- so the free-text
    # field holds it too, and the answer on screen is the whole answer given.
    typed: str
    # Whether an answer of this kind takes one column, so the picker offers
    # radio buttons rather than checkboxes. Read off `Option.declares_test`:
    # an option that declares dbt's `unique` test checks one column, and the
    # engine refuses two. Checkboxes let a reader tick two and be refused.
    single: bool = False


@dataclass(frozen=True, slots=True)
class Asking:
    """One question as the workbench shows it: the question and its answers.

    `index` is the question's place in the walk, and the anchor the screen
    comes back to after an answer: `/everything#question-<index>`.

    `example` is the worked example for this question, or None where the
    engine refuses one. It is the single most effective element the
    walkthroughs measured, so the one screen shows it too. A link to the
    question's own screen did not: in direct mode that screen sends the
    reader back here, so the example never showed.
    """

    decision: Decision
    choices: tuple[Choice, ...]
    index: int
    example: Example | None
    # The Decisions this question's answer put on the record -- see
    # `_produced_by`. Direct mode has no question screens, so this is the one
    # place they show there; the caveats screen leaves them out in both modes.
    produced: tuple[Decision, ...] = ()


@dataclass(frozen=True, slots=True)
class Describable:
    """One model the describe screen asks about, and what the reader has
    said it is for.

    `description` is "" until they write one, and it is theirs: the engine
    carries it and never fills it. What a model is *for* is not in the SQL
    that builds it -- the SQL says what it computes -- so a suggested
    description would be this tool putting words in a reader's mouth on the
    one screen that asks for theirs.
    """

    model: AssembledModel
    description: str
    # What this model is materialized as right now -- the conversion's own
    # answer unless the reader has changed it. The select shows it as the
    # current one so a reader is choosing against what they have, not against
    # a blank.
    materialization: str
    # Every materialization this walk offers, so the template lists the
    # engine's set rather than one written out in HTML beside it.
    choosable: tuple[str, ...]
    # The tags on this model, space-separated, as the box takes them back.
    # Read off the model rather than off the session, so the box shows what
    # the conversion carries -- stripped, deduplicated, in order -- and not
    # the raw string somebody typed. A reader who types a tag twice sees it
    # once, which is what dbt will get.
    tags: str
    # What the reader typed into this row when the engine refused the save,
    # with the refusal, for one render. The boxes show the draft rather than
    # what is held, so the reader corrects one field instead of typing all
    # three again. None on every other row and every other render.
    draft: Draft | None = None


@dataclass(frozen=True, slots=True)
class Draft:
    """One refused save of a describe row: what was typed, and why not.

    `message` is the engine's refusal. The three fields are the reader's own
    text, as they sent it -- not what the session holds, because the session
    holds nothing of a refused save.
    """

    model: str
    message: str
    text: str
    tags: str
    materialization: str


@dataclass(frozen=True, slots=True)
class Refused:
    """One refused answer, held until the refusal page renders it once.

    `back` is where that page sends the reader: the question it refused, or
    the start when no question has that key.
    """

    message: str
    back: str


@dataclass(frozen=True, slots=True)
class Conversion:
    """One model beside the SQL it was built from.

    `before` is every statement that shaped this model, verbatim as the
    reader wrote it, in the order the file had them. More than one is
    ordinary and is the case worth showing: a TRUNCATE and the INSERT that
    refills it are two statements a reader typed and one model dbt runs.

    Every statement, which means `folded_indices` as well as
    `source_indices`. A GRANT is attached to a draft that already exists, so
    it belongs to neither the model's own sources nor the caveats screen's
    orphans -- and it puts a `grants={...}` line in the file on the right.
    Left out, this screen showed a reader a line of config and nothing on
    the page saying which of their statements asked for it, on the one
    screen whose question is "is this still my query?".

    `after` is the model file's text, rendered the same way the files screen
    renders it -- by `render_model`, not by a second copy of it -- so the two
    screens cannot disagree about what is about to be written.
    """

    model: AssembledModel
    before: tuple[str, ...]
    after: str


@dataclass(frozen=True, slots=True)
class FilePreview:
    """One file this conversion will write, and what will be in it."""

    path: str
    contents: str


# What a write can fail with that the reader can do something about: a
# destination inside their own dbt project, a model path that would escape
# the destination, and every OSError a directory can raise -- a permission,
# a file standing where a directory belongs, a disk that is full -- and a
# previous write into the destination whose files cannot be cleared, because
# one of them was edited since or its record is damaged (`StaleOutputError`).
#
# `emit` raises two more. `DuplicateSourceEntryError` and
# `OrphanSchemaTestError` are each documented as unreachable through the
# pipeline, so reaching one is a defect in this tool; a screen telling the
# reader to check their own paths over one of those would be this tool
# blaming them for its own bug. They are absent here for the same reason they
# are absent from the CLI's `_USAGE_ERRORS`, and absent means they surface as
# a traceback.
_WRITE_FAILURES = (OutputInsideProjectError, UnsafeOutputPathError, StaleOutputError, OSError)

# What a delivery can fail with that a reader can do something about: their
# project is not a git repository or not the root of one, their working tree
# has changes in it, the branch name is taken. GitFailedError is here too and
# is the odd one -- it means a git command failed after its own precondition
# was checked, which is a repository in a state this tool does not understand.
# A reader can still act on it, because it carries git's own words, and the
# alternative is a traceback on the last screen of the walk.
# What a push can fail with that a reader can do something about: their
# project has no remote of that name, or git itself refused -- a rejected
# push, a credential they have not set up, a server that is not there. Every
# one of those is theirs to fix, and git's own words are the best account of
# it, so `GitFailedError` is here rather than surfacing as a traceback on the
# last screen of the walk.
_PUSH_FAILURES = (NoRemoteError, NotAGitRepoError, GitFailedError)

_DELIVER_FAILURES = (
    NotAGitRepoError,
    DirtyWorkingTreeError,
    BranchExistsError,
    GitFailedError,
    ValueError,
)


def _branch_name(change: ProjectChange) -> str:
    """The branch a delivery of `change` would make.

    Named after the models it carries rather than after a clock: a reader
    reading `git branch` a week later needs to know which conversion a branch
    holds, and a timestamp tells them when they pressed a button. One model
    names itself; more than one is counted, because a branch name listing
    nine models is a branch name nobody reads.
    """
    names = [model.name for model in change.models]
    if len(names) == 1:
        return f"dbtw/{names[0]}"
    return f"dbtw/{len(names)}-models"


def _commit_message(change: ProjectChange) -> str:
    """The commit a delivery makes, in the shape a reviewer reads first.

    A subject naming what arrived, and a body listing the models, because the
    conversion's own reasoning is in CONVERSION_REPORT.md beside them and a
    commit message repeating it would be a second copy free to disagree.
    """
    models = sorted(model.name for model in change.models)
    noun = "model" if len(models) == 1 else "models"
    listed = "\n".join(f"- {name}" for name in models)
    return f"Convert {len(models)} {noun} with dbt training wheels\n\n{listed}\n"


class NoConversationError(RuntimeError):
    """A screen of the walk reached for a session before one existed.

    Not a usage error and deliberately not in the CLI's `_USAGE_ERRORS`: no
    input produces it. Every screen reaches its session through `_view`,
    which redirects to the entry screen while `Source.session` is None, so
    this is a route that skipped that guard -- a defect in this module, and
    it surfaces as one.
    """


@dataclass(frozen=True, slots=True)
class Written:
    """One completed write: where it went, what landed, and what it was of.

    `change` is what was written, not a flag saying that something was. The
    write action is terminal for a conversion and not for the walk: a reader
    can go back, answer a question differently, and press it again, and the
    files they now have on screen are the ones that have to reach disk. Held
    as the change itself, a second press compares what is on screen against
    what was written and writes only when those differ -- so a double click
    writes once and a changed answer writes again, which a boolean cannot
    tell apart.

    `removed` is what this write took away: the files the previous write
    into `out` produced and this one does not -- a model an answer moved to
    another layer, left behind in the one it left. Read off
    `EmitResult.removed`, as `files` is read off `EmitResult.paths`.
    """

    out: Path
    files: tuple[str, ...]
    change: ProjectChange
    removed: tuple[str, ...] = ()


def noun(count: int, singular: str) -> str:
    """The noun beside a count, in the number the count is in.

    Every tally on every screen read "<n> models", so a conversion of one
    query said "1 models". Each noun on these screens takes a plain "s", which
    is what this assumes; a noun that does not would need its plural passed
    in, and none here does.
    """
    return singular if count == 1 else f"{singular}s"


def _columns(candidates: Sequence[str], given: Sequence[str]) -> tuple[Column, ...]:
    seen: set[str] = set()
    rows: list[Column] = []
    for name in candidates:
        tick = name not in seen
        rows.append(Column(name=name, tick=tick, checked=tick and name in given))
        seen.add(name)
    return tuple(rows)


def _choices(
    decision: Decision,
    prompts: dict[str, str],
    given: tuple[str, tuple[str, ...]] | None,
    draft: tuple[str, tuple[str, ...]] | None = None,
) -> tuple[Choice, ...]:
    """The answers on one question's screen, with the reader's own answer in
    its picker. `given` is what `Session.answers` holds for this question --
    `(Option.kind, columns)` -- or None while it is unanswered.

    `draft` is a refused answer to this question, in the same shape, for the
    one render the refusal page links to. Its option's picker shows what the
    reader ticked and typed, so they correct it instead of starting again.
    It fills that picker and changes nothing else: the question is still
    answered or unanswered as `given` says.
    """
    candidates = decision.subject.candidates if decision.subject is not None else ()
    keyed = decision.subject.columns if decision.subject is not None else ()
    choices: list[Choice] = []
    for option in decision.options:
        asks = bool(prompts.get(option.kind))
        # An option the pristine question asks columns for and this one does
        # not is an option the engine has spelled with the key the answer
        # supplied: after "merge on event_id, checked on every run", the
        # plain merge option reads "merge on event_id" too. Its picker holds
        # that key, so pressing it sends the answer its label names rather
        # than no columns at all.
        spelled = asks and not option.columns_prompt
        if draft is not None and draft[0] == option.kind:
            mine = draft[1]
        elif given is not None and given[0] == option.kind:
            mine = given[1]
        elif spelled:
            mine = keyed
        else:
            mine = ()
        choices.append(
            Choice(
                option=option,
                chosen=option.label == decision.chosen,
                prompt=prompts.get(option.kind, ""),
                columns=_columns(candidates, mine) if asks else (),
                typed=", ".join(c for c in mine if c not in candidates) if asks else "",
                answered=given is not None,
                single=option.declares_test == "unique",
            )
        )
    return tuple(choices)


@dataclass(frozen=True, slots=True)
class Consequence:
    """One explanation, and every action this conversion took under it.

    Three references rewritten in three models produce three Decisions
    carrying three different actions and one identical reason. Rendered a
    Decision at a time, that reason is on the page three times, differing in
    nothing -- the shape a reviewer reading a real three-model report named
    as the thing most likely to train a reader to skip the block. Grouped,
    every action is still listed and the explanation is read once.

    Grouped on the pair, not on the dbt-native half alone: two Decisions that
    explained themselves identically in dbt's words and differently in plain
    ones would lose one of the plain wordings.
    """

    reason: str
    plain_reason: str
    decisions: tuple[Decision, ...]


def _grouped(decisions: Sequence[Decision]) -> tuple[Consequence, ...]:
    """`decisions`, in pipeline order, with identical explanations merged."""
    order: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[Decision]] = {}
    for decision in decisions:
        signature = (decision.reason, decision.plain_reason)
        if signature not in grouped:
            grouped[signature] = []
            order.append(signature)
        grouped[signature].append(decision)
    return tuple(
        Consequence(reason=reason, plain_reason=plain, decisions=tuple(grouped[(reason, plain)]))
        for reason, plain in order
    )


def _elsewhere(session: Session, *, without: str = "") -> frozenset[str]:
    """The Decision keys this conversion records with the held answers, less
    `without`'s -- or with none of them at all when `without` is empty.

    A second `Session` over the same inputs, built through the public
    constructor. It is not a second code path: the same pipeline runs, on the
    same inputs, with a different answer set, which is the only way to ask
    what one answer is responsible for.

    Everything but the answers is carried across, and that is not optional.
    The other projects a reader named decide which questions the run even
    asks -- a cross-project question exists only while its table is still a
    source -- so a rebuild that dropped them would ask a different set, and a
    held answer to a question this run no longer has raises `UnknownAnswerError`
    straight through the route as a 500. Descriptions, tags and
    materializations are carried for the quieter version of the same reason:
    a materialization override records a caveat Decision, and a rebuild
    without it would attribute that caveat to whichever answer this is asking
    about. The only thing that differs is the one answer removed. `answers` is
    copied rather than shared, so nothing this asks can change what the
    session holds.
    """
    held = {key: value for key, value in session.answers.items() if key != without}
    other = Session(
        project=session.project,
        sql=session.sql,
        dialect=session.dialect,
        answers=held if without else {},
        descriptions=dict(session.descriptions),
        tags=dict(session.tags),
        materializations=dict(session.materializations),
        elsewhere=session.elsewhere,
    )
    return frozenset(decision.key for decision in other.view().change.decisions)


def _produced_by(session: Session, change: ProjectChange, key: str) -> tuple[Decision, ...]:
    """The Decisions this question's answer put on the record.

    An answer the engine accepts and then does not apply is the case this
    exists for: name a key the model does not select and the conversion comes
    back as an append, the screen shows "append every row -- chosen", and the
    only thing that says why is a Decision two screens away, in dbt's words.
    The engine is not silent there; the walk was.

    Derived by difference rather than by reading a name out of a key: the
    Decision an answer produces carries no statement index and its key ends
    in the table's name, so pairing it to the question by spelling would be
    this layer parsing our own identifiers -- and `events` sits inside
    `stg_events`, which is how that goes wrong. Running the conversion
    without this one answer and taking what appears when it is put back is
    exact, and it costs one more pipeline run on a screen that shows one
    question.

    Empty for an unanswered question, and empty for an answer the engine
    applied as given -- there is nothing to say about an answer that did what
    it said.
    """
    if key not in session.answers:
        return ()
    without = _elsewhere(session, without=key)
    return tuple(d for d in change.decisions if d.key not in without)


def _answered_into_existence(session: Session, change: ProjectChange) -> frozenset[str]:
    """Every Decision key this conversion records only because of an answer.

    The caveats screen is everything this conversion decided without asking,
    and a Decision that exists because of an answer belongs beside the
    question that answer was given to -- so this is what the caveats screen
    subtracts and the question screens add back. Between them every Decision
    still reaches exactly one screen, and
    `test_every_decision_reaches_exactly_one_screen` is what holds that in the
    answered states as well as the pristine one.
    """
    if not session.answers:
        return frozenset()
    pristine = _elsewhere(session)
    return frozenset(d.key for d in change.decisions if d.key not in pristine)


def _renames(change: ProjectChange) -> tuple[Decision, ...]:
    return tuple(d for d in change.decisions if d.key.startswith(_RENAME))


def _caveats(change: ProjectChange, answered: frozenset[str]) -> tuple[Decision, ...]:
    """Everything this conversion decided without asking, that an answer did
    not bring into existence."""
    return tuple(
        d
        for d in change.decisions
        if not d.question and not d.key.startswith(_RENAME) and d.key not in answered
    )


def _example_for(decision: Decision, change: ProjectChange) -> Example | None:
    """The example for this question, drawn against the model built from the
    statement it came from.

    `worked_example` refuses a mismatched pair itself, but it must not be
    handed one: a caller that guesses at the model is asking for a confident
    picture of the wrong table. So every model is offered and the first one
    the engine accepts is the answer -- the pairing is the engine's, made on
    statement index, and this loop only carries the candidates to it.
    """
    for model in change.models:
        example = worked_example(decision, model, change.dialect)
        if example is not None:
            return example
    return None


def _previews(
    session: Session,
    change: ProjectChange,
    progress: Progress = NOTHING_EXPLAINED,
    remember: bool = True,
) -> tuple[FilePreview, ...]:
    """Every file this conversion would write, with its contents.

    Written by `emit` into a directory that is thrown away, and read back,
    rather than re-derived here. Where a sources file lands and whether a
    per-model schema .yml is written are decisions `emit` makes, and a second
    copy of that reasoning in this module would be free to show a file list
    that disagreed with the one `/write` produces -- which is precisely the
    mismatch that cost this page the most trust when it was wrong: "that's
    the exact kind of mismatch that would make me stop trusting every other
    file preview on the page too."
    """
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "preview"
        result = emit(
            change,
            read_project(session.project),
            out,
            progress,
            sql_path=session.sql,
            remember=remember,
        )
        return tuple(
            FilePreview(
                path=path.relative_to(out).as_posix(),
                contents=path.read_text(encoding="utf-8"),
            )
            for path in result.paths
        )


def _describables(change: ProjectChange, draft: Draft | None = None) -> tuple[Describable, ...]:
    """Every model this conversion builds, each with the description held for
    it, in the order the conversion carries them.

    Every model, not only the ones still blank. A screen that dropped a model
    the moment it was described would move the rows under the reader as they
    worked down the page, and would give them no way to read back or correct
    what they had written.

    `draft` goes on the row of the model it names. A draft for a model this
    conversion does not build goes on no row; the screen shows its refusal
    above the rows instead.
    """
    written = {entry.model: entry.text for entry in change.descriptions}
    return tuple(
        Describable(
            model=model,
            description=written.get(model.name, ""),
            materialization=model.materialization or "",
            choosable=CHOOSABLE,
            tags=" ".join(model.tags),
            draft=draft if draft is not None and draft.model == model.name else None,
        )
        for model in change.models
    )


def _stray(draft: Draft | None, rows: Sequence[Describable]) -> str:
    """The refusal of a draft that sits on no row, or "".

    A form that names a model this conversion does not build is refused by
    the engine in its own words, and there is no row to put that beside.
    """
    if draft is None or any(row.draft is not None for row in rows):
        return ""
    return draft.message


class MissingOriginalError(RuntimeError):
    """A model names a statement this conversion did not read.

    Not reachable through one request: the change and the statements are read
    from the same path microseconds apart, and `assemble` carries the indices
    `run_passes` assigned. Raised rather than skipped because the alternative
    is a screen that puts one model beside another model's SQL and says it is
    the before -- the most convincing wrong thing this walk could show.
    """


def _conversions(
    change: ProjectChange, originals: Sequence[ClassifiedStatement]
) -> tuple[Conversion, ...]:
    """Every model beside the statements it was built from.

    Joined on the indices the model carries -- `source_indices` for what it
    was built from and `folded_indices` for what was folded into it -- which
    name positions in `originals`. The pairing is the engine's; this looks it
    up and nothing more. A statement that survived into no
    model is not here, because there is no after to put beside it: those are
    the caveats screen's, which is where a reader is told what happened to
    them.
    """
    conversions: list[Conversion] = []
    for model in change.models:
        texts: list[str] = []
        for index in sorted((*model.source_indices, *model.folded_indices)):
            if not 0 <= index < len(originals):
                raise MissingOriginalError(
                    f"{model.name} was built from statement {index}, and this "
                    f"conversion read {len(originals)}. The models and the statements "
                    "come from one path read twice; an index out of range means they "
                    "disagree, and a before shown from the wrong statement is worse "
                    "than none"
                )
            texts.append(originals[index].raw.text)
        conversions.append(Conversion(model=model, before=tuple(texts), after=render_model(model)))
    return tuple(conversions)


def _number_repeats(screens: tuple[Screen, ...]) -> tuple[Screen, ...]:
    """The same steps, with a number on each one whose label and kind repeat.

    A question step is named by its table and a kind word. Two questions of
    one kind about one table -- two appends into one table, for example --
    would read the same in the rail, so each of those gets its position
    among them, in rail order. A step whose label is unique keeps no number:
    a "1" beside the only step of its name would suggest a second one.
    """
    names = [(screen.label, screen.kind) for screen in screens]
    seen: dict[tuple[str, str], int] = {}
    numbered: list[Screen] = []
    for screen, name in zip(screens, names, strict=True):
        if names.count(name) < 2:
            numbered.append(screen)
            continue
        seen[name] = seen.get(name, 0) + 1
        numbered.append(dataclasses.replace(screen, nth=str(seen[name])))
    return tuple(numbered)


def _screens(
    view: SessionView,
    answered: Container[str] = (),
    described: str = "stands",
    direct: bool = False,
) -> tuple[Screen, ...]:
    """The walk, in order. One screen per question, and seven fixed ones.

    "Your project" comes first because everything after it is a proposal
    about that project, and a reader who has not seen what this tool believes
    about their conventions has no way to judge any of it.

    Describe sits after the questions and before the files, because it is the
    last thing the reader supplies and the first thing that is theirs alone:
    every question above it offers options the engine wrote, and this one
    offers a blank box. Putting it after the files screen would ask them to
    describe models they had already read the finished text of.
    """
    questions = _number_repeats(
        tuple(
            Screen(
                url=f"/questions/{index}",
                label=decision.subject.table if decision.subject else decision.key,
                engine=True,
                state=_question_state(decision, answered),
                kind=_kind(decision),
            )
            for index, decision in enumerate(view.questions)
        )
    )
    if direct:
        # One screen where the guided walk has a screen per question and a
        # screen for the models. The design calls it the workbench and
        # promises "same conversion, everything on one screen" -- so this
        # replaces which screens exist and nothing else. Its dot is the
        # loudest of the ones it stands in for: a reader who collapsed the
        # walk still has to be told something is unanswered.
        asking = [screen.state for screen in questions] + [described]
        return (
            Screen(url="/project", label="Your project", engine=False),
            Screen(url="/", label="Start", engine=False),
            Screen(
                url="/everything",
                label="Everything",
                engine=False,
                state="ask" if "ask" in asking else "ok",
            ),
            Screen(url="/caveats", label="Decided for you", engine=False),
            Screen(url="/changed", label="What changed", engine=False),
            Screen(url="/files", label="Files", engine=False),
            Screen(url="/done", label="Done", engine=False),
        )
    return (
        Screen(url="/project", label="Your project", engine=False),
        Screen(url="/", label="Start", engine=False),
        *questions,
        Screen(url="/describe", label="Describe", engine=False, state=described),
        Screen(url="/caveats", label="Decided for you", engine=False),
        Screen(url="/changed", label="What changed", engine=False),
        Screen(url="/files", label="Files", engine=False),
        Screen(url="/done", label="Done", engine=False),
    )


# The word the rail shows for each kind of question, keyed by the pass that
# asks it. A closed list: every question the engine can ask comes from one of
# these five passes -- `tier2.append.`, `tier2.merge.`, `assemble.layer.`,
# `assemble.variable.` and `assemble.cross_ref.` are the only keys built with
# `question=`. The raw pass name is not a reader's word: `cross_ref` named a
# key, and the question is about a table in another project.
# `test_every_question_the_corpus_asks_has_a_plain_kind_word` walks every
# question the test corpus asks and fails on a kind that is not here.
#
# Each word names what the question is about, never an answer to it. The
# append question was "append", which is also one of its answers -- so the
# rail read "revenue_events append" after the reader had answered merge.
_KIND_WORDS = {
    "append": "new rows",
    "merge": "merge key",
    "layer": "layer",
    "variable": "variable",
    "cross_ref": "another project",
}


def _kind(decision: Decision) -> str:
    """The word for the kind of question `decision` asks, or "".

    Read off the second segment of the documented key shape,
    "<stage>.<pass>.<subject>" -- the same contract `_RENAME` reads -- and
    looked up in `_KIND_WORDS`. Total over every key: a key of another shape,
    or a pass the list does not name, gives no word, so the step is labelled
    by its table alone and every screen still renders. The corpus test is
    where such a pass fails, loudly, before it reaches a reader.
    """
    parts = decision.key.split(".", 2)
    if len(parts) < 3:
        return ""
    return _KIND_WORDS.get(parts[1], "")


def _code_spans(text: str) -> Markup:
    """`text` with each backticked span rendered as `<code>`.

    Engine text marks code with backticks, as the report does in Markdown:
    a value a reader types in the glossary (`staging`), a token and a flag in
    a parse reason (`--dialect postgres`), a line of YAML in a caveat. A
    screen is not Markdown, so it showed the backticks themselves. Every
    screen renders its engine prose through this filter.

    Backticks pair from the left. A last backtick with no partner opens no
    span and is shown as the character it is: engine text can carry what a
    reader typed -- a refused name is quoted back to them -- and a backtick
    they typed is text, not markup. `tests/unit/web/page.as_shown` reads a
    run back by the same rule.
    """
    parts = text.split("`")
    unpaired = len(parts) % 2 == 0
    spans = parts[:-1] if unpaired else parts
    # Two backticks with nothing between them mark no code, so they are
    # shown as the two characters, not as an empty <code>.
    shown = Markup("").join(
        (Markup("<code>{}</code>").format(part) if part else escape("``"))
        if index % 2
        else escape(part)
        for index, part in enumerate(spans)
    )
    if unpaired:
        shown += escape("`" + parts[-1])
    return shown


def _path_breaks(path: str) -> Markup:
    """`path` as one span per folder, with a line break offered after each
    separator.

    A long output path broke mid-word, because the only place it could break
    was anywhere -- and a folder name with hyphens in it breaks at a hyphen
    too. Each folder name, with the "/" after it, is a span the stylesheet
    keeps on one line; `<wbr>` offers the break between two of them. Neither
    adds text, so the run still reads as the path itself, and the
    exact-match check on the screen still holds it.
    """
    parts = path.split("/")
    segments = [f"{part}/" for part in parts[:-1]] + [parts[-1]]
    return Markup("<wbr>").join(
        Markup('<span class="segment">{}</span>').format(segment) for segment in segments if segment
    )


def _question_state(decision: Decision, answered: Container[str]) -> str:
    """The dot beside one question: asking until the reader has answered it.

    Two outcomes, and it is deliberately not three. Every question here
    arrives with a `chosen` already on it -- the engine reads what the SQL
    does and proposes that -- so keying the dot on `chosen` would mark the
    whole walk "defaults stand, safe to skip" and there would be nothing
    left for the red one to mean. And it would be false: a question exists
    in this walk precisely because the engine decided it should not decide
    alone. What the SQL does is not yet what the reader meant, and only they
    can close that gap.

    Which leaves the hollow dot for the screens that genuinely have nothing
    to decide -- the start, the caveats, the files, the last one -- which is
    what it says.
    """
    return "ok" if decision.key in answered else "ask"


def _described_state(change: ProjectChange) -> str:
    """The dot for the describe screen.

    "ask" while a model this conversion builds has no description, because
    that is the one thing on the walk the engine cannot supply and the design
    calls the field it insists on. "ok" once none is blank. Never "stands":
    a blank description is not a default that holds, it is a sentence nobody
    has written -- and the write action stays reachable regardless, which is
    where this walk keeps faith with the three personas who called a gate
    theatre.
    """
    if not change.models:
        return "stands"
    described = {entry.model for entry in change.descriptions}
    return "ok" if all(model.name in described for model in change.models) else "ask"


def _spoken(*texts: Iterable[str] | str) -> str:
    """One string holding everything a screen renders from the engine.

    `terms_in` reads it, so a screen defines the dbt words it actually uses
    and no others -- four of five personas never reached the round-one block
    that defined everything on one page.
    """
    parts: list[str] = []
    for text in texts:
        if isinstance(text, str):
            parts.append(text)
        else:
            parts.extend(text)
    return "\n".join(part for part in parts if part)


def _terms(spoken: str) -> tuple[Term, ...]:
    """The glossary entries `spoken` uses, in the order it first uses them.

    One line, and it stays one line: what makes this honest is that `spoken`
    is everything the screen renders from the engine and nothing else, so the
    block defines the words a reader meets on that screen and no others.
    """
    return terms_in(spoken)


def _learned(source: Source) -> Progress:
    """What earlier conversions of this walk's project already explained.

    Empty while there is no conversation, which is the entry screen: there is
    no project yet, so there is nothing this reader can be said to have met.
    Read per request rather than held on `Source`, because a reader who
    deletes the record mid-walk has deleted it, and a cached copy would go on
    folding words the tool no longer claims to have taught.
    """
    if not source.remember or source.session is None:
        return NOTHING_EXPLAINED
    return load_progress(source.session.project)


def create_app(source: Source) -> Flask:
    """The walk over one conversation, and the screen that starts one.

    Keyed by nothing, as spec 4.1 requires: this is a local single-user tool
    and a second tab is a second view of the same conversation.

    `source` holds which conversation that is, and it is the one mutable
    thing here. A `dbtw web` given a SQL_PATH arrives with a session already
    open; one given none arrives with `source.session` None, and every screen
    of the walk sends a reader to `/source` until SQL has arrived. The walk
    itself does not change shape either way -- it renders a conversation, and
    where the conversation came from is not its business.

    `source.out` is where the write action writes, and it is the caller's
    rather than the reader's: `dbtw web --out` names it, the done screen
    shows it before the button, and nothing on a screen can change it. A
    destination typed into the browser would be one more thing for a
    first-time user to get right at the one moment in the walk that touches
    their disk -- and the session holds it nowhere, because a conversation
    about a conversion is not where the result goes.
    """
    app = Flask(__name__)
    app.jinja_env.filters["noun"] = noun
    app.jinja_env.filters["code"] = _code_spans
    app.jinja_env.filters["path"] = _path_breaks
    # The three strings a rendered example is shown under. `emit.example`
    # owns them because each is a claim only that module can vouch for --
    # that the values are placeholders, that the first block is a premise
    # rather than a row this engine read, and that what acts on it is this
    # model rather than the reader's script. A template writing its own
    # wording for any of them would be authoring the claim.
    # The walk's own state, reachable from the app. A test checking that a
    # screen shows what the engine produced has to be able to ask the engine,
    # and `Source` is where the answers this walk cannot be asked twice for
    # are kept -- see `Source.pushed`.
    app.config["dbtw_source"] = source

    app.jinja_env.globals.update(
        example_notice=PLACEHOLDER_NOTICE,
        supposed_label=SUPPOSED_ROW_LABEL,
        after_label=AFTER_RUN_LABEL,
    )

    @app.context_processor
    def _bar() -> dict[str, str]:
        """The app bar names the project every screen converts against.

        A context processor rather than a global, because it changes: a
        reader gives their project on the entry screen, and the bar has to
        show the one they gave rather than the one the process started with.
        Empty until they have, so the bar shows nothing rather than a
        placeholder for a project nobody has chosen. An identifier the bar
        displays, never prose it speaks.
        """
        return {
            "project_path": str(source.project) if source.project else "",
            "mode": source.mode,
        }

    # A refused answer, held from the POST that was refused to the one GET
    # that shows it. The POST redirects, so the refusal has an address and a
    # reload asks nothing of the browser. `refused_back` outlives it: a
    # reload of the refusal page after it has rendered goes back to the
    # question, which is the way on the page itself offered.
    refused: Refused | None = None
    refused_back = "/"

    # A refused describe save, held the same way for the screen that row is
    # on, so the reader lands back beside the row with what they typed.
    drafted: Draft | None = None

    @app.before_request
    def _hand_over() -> None:
        """Give each held refusal to the one request after the POST that made
        it, and to no later one.

        The POST redirects, and the GET it redirects to is the next request.
        Moving both slots onto that request -- and emptying them -- means a
        refusal is shown by the first page that renders, or by none. Held
        until its own screen rendered, a refusal whose redirect was never
        followed came back with a 400 on a later, unrelated visit: after a
        successful save, or in a new conversation.
        """
        nonlocal refused, drafted
        g.refused, refused = refused, None
        g.drafted, drafted = drafted, None

    def _take_draft() -> Draft | None:
        """The draft handed to this request, if any. See `_hand_over`."""
        draft: Draft | None = g.drafted
        return draft

    def _conversation() -> Session:
        """The session this app is serving.

        Every caller reaches this behind `_view`, which sends a reader to the
        entry screen while there is none, so the None here is a bug in this
        module rather than a state a request can be in. A raise and not an
        assert for the reason this package raises everywhere else: `python
        -O` strips an assert, and what silence would leave is an
        AttributeError on None three frames further in.
        """
        session = source.session
        if session is None:
            raise NoConversationError(
                "no SQL has been given to this walk yet; every screen reaches a "
                "session through _view, which redirects to the entry screen while "
                "there is none, so reaching here means one of them does not"
            )
        return session

    def _stale() -> tuple[str, int] | None:
        """The screen for the states in which nothing else can render.

        Every accessor raises while the reader holds something the current SQL
        no longer has a place for -- the script was edited underneath it.
        There are two such things, and each has one accessor that still
        answers, so the screen can say what it is about and offer the way out
        instead of showing an error with no subject and no way on.

        Answers first, and the order is not a preference. A held answer that
        cannot be resolved is a question this conversion no longer asks, and
        until it is dropped the models it would have named are not knowable --
        so `stale_descriptions` raises exactly as everything else does, and
        asking it first would turn the screen that gets a reader out into one
        more page that cannot render.
        """
        session = _conversation()
        stale = session.stale_answers()
        if stale:
            return _stranded("stale.html", "Answers with no question", "answer", stale)
        described = session.stale_descriptions()
        if described:
            return _stranded(
                "stale_descriptions.html", "Descriptions with no model", "model", described
            )
        tagged = session.stale_tags()
        if not tagged:
            return None
        return _stranded("stale_tags.html", "Tags with no model", "model", tagged)

    def _stranded(
        template: str, heading: str, counted: str, stale: Sequence[str]
    ) -> tuple[str, int]:
        """One stranded screen, with the glossary of the words it shows.

        `heading` and `counted` are the template's own heading and tally
        noun, given here so the glossary reads them: each stranded template
        writes its heading itself, for the reason its header gives. With no
        glossary, a screen that says "Descriptions with no model" defined no
        word, though "model" is a word the glossary can define.
        """
        spoken = _spoken(heading, noun(len(stale), counted), stale)
        return render_template(
            template,
            stale=stale,
            screens=(),
            teaching=teaching_for(_terms(spoken), _learned(source)),
            here="",
        ), 409

    def _view() -> SessionView | tuple[str, int] | Response:
        """This conversation's view, or the page to render instead of one.

        Three outcomes, and every screen of the walk returns whichever it
        gets: the view, the stranded screen, or -- before any SQL has arrived
        -- a redirect to the entry screen. The last one is why no route needs
        its own check for an unstarted walk: the guard every screen already
        has for the stranded case absorbs it.
        """
        if source.session is None:
            return redirect(url_for("entry"))
        try:
            return _conversation().view()
        except (UnknownAnswerError, UnknownModelError):
            stranded = _stale()
            if stranded is None:
                raise
            return stranded

    def _entry_page(refusal: str, typed: dict[str, str] | None = None, box: str = "") -> str:
        """The screen a reader meets before there is a conversion.

        It speaks, and every sentence on it is `dbtw.core.intro`'s. That is
        not an exemption from the prose rule but the rule applied: a screen
        may not write a sentence, so the four things this tool does live in
        the engine beside every other claim it makes, and this renders them.
        """
        spoken = _spoken(
            HEADLINE,
            LEDE,
            (pillar.name for pillar in PILLARS),
            (pillar.plain for pillar in PILLARS),
            PRIVACY,
            refusal,
        )
        return render_template(
            "entry.html",
            screens=(),
            teaching=teaching_for(_terms(spoken), _learned(source)),
            here="/source",
            started=source.session is not None,
            # What the reader typed, when this is a refusal of it: sending
            # them back to an empty box after a refusal about one field would
            # make them retype the other three.
            project=typed["project"] if typed else (str(source.project) if source.project else ""),
            elsewhere=typed["elsewhere"]
            if typed
            else "\n".join(str(path) for path in source.elsewhere),
            pasted=typed["pasted"] if typed else "",
            name=typed["name"] if typed else "",
            headline=HEADLINE,
            lede=LEDE,
            pillars=PILLARS,
            privacy=PRIVACY,
            refusal=refusal,
            # Which box the refusal is about -- "project", "elsewhere" or
            # "name" -- so the screen can put that box's label in front of
            # it. "" for a refusal about none of them.
            box=box,
        )

    def _walk(view: SessionView) -> tuple[Screen, ...]:
        """This walk's screens with their dots filled in.

        Every screen asks for the rail, and the rail's dots are derived from
        the conversation rather than from the route -- so there is one place
        that knows how a dot is decided, and twenty-two call sites that do
        not have to.
        """
        return _screens(
            view,
            answered=_conversation().answers,
            described=_described_state(view.change),
            direct=source.mode == "direct",
        )

    @app.get("/source")
    def entry() -> str:
        """Where a reader brings their SQL in.

        The one screen of this app that is not about a conversion, because
        there is not one yet. It is held to every rule the walk's screens are
        held to, the prose rule included, and it passes: explaining no
        Decision, it has nothing to say at sentence length, and what it writes
        are labels. The one assertion it is exempt from is that a page shows a
        derived count -- it has nothing to count, and a nav reading "0 screens"
        would report the absence of a conversation as a defect.
        """
        return _entry_page(refusal="")

    @app.post("/source")
    def convert() -> str | tuple[str, int] | Response:
        """Take what the reader brought and open a conversation over it.

        Pasted text and chosen files are one input here: both end up as
        files in one directory, and `ingest` reads a directory as every .sql
        in it, so a folder of scripts and a single pasted query differ in
        nothing this route has to know about.

        A refusal re-renders this screen with the reason on it rather than
        redirecting, for the same reason the answer loop's refusal does: the
        reader is mid-action and the thing they need is on the page they were
        already on.
        """
        nonlocal refused_back
        project = request.form.get("project", "")
        pasted = request.form.get("pasted", "")
        uploaded = {
            Path(storage.filename).name: storage.read().decode("utf-8", errors="replace")
            for storage in request.files.getlist("files")
            if storage.filename
        }
        files = dict(uploaded)
        name = request.form.get("name", "")
        elsewhere = request.form.get("elsewhere", "")
        # One per line. A reader with three other projects types three paths,
        # and blank lines between them are how people type lists.
        others = [line.strip() for line in elsewhere.splitlines()]
        try:
            if pasted.strip():
                staged_as = pasted_file_name(name)
                # One name is one file on disk, so a paste named like a file
                # chosen beside it would replace that file -- or be replaced
                # by it -- with nothing saying which.
                if staged_as in files:
                    raise UnnamedPasteError(
                        f"{staged_as} is already the name of a chosen file. Give the pasted "
                        "SQL a different name, so that each file becomes a model of its own."
                    )
                files[staged_as] = pasted
            source.start(project, files, [line for line in others if line])
        except ProjectPathError as refusal:
            typed = {"project": project, "elsewhere": elsewhere, "pasted": pasted, "name": name}
            return _entry_page(refusal=str(refusal), typed=typed, box=refusal.box), 400
        except UnnamedPasteError as refusal:
            # About the file name box, so the screen puts that box's label in
            # front of it, as it does for the two path boxes.
            typed = {"project": project, "elsewhere": elsewhere, "pasted": pasted, "name": name}
            return _entry_page(refusal=str(refusal), typed=typed, box="name"), 400
        except (
            NotADbtProjectError,
            EmptySourceError,
            OSError,
            UnicodeDecodeError,
        ) as refusal:
            typed = {"project": project, "elsewhere": elsewhere, "pasted": pasted, "name": name}
            return _entry_page(refusal=str(refusal), typed=typed), 400
        # A new conversation has none of the old one's questions, so a reload
        # of an old refusal page goes to the start, not to an old question.
        refused_back = url_for("start")
        return redirect(url_for("start"))

    @app.get("/project")
    def project() -> str | tuple[str, int] | Response:
        """What this walk read out of the reader's dbt_project.yml.

        Every proposal the rest of the walk makes rests on this: which layer
        a model belongs in, what prefix its name takes, what a folder already
        materializes as. `read_project` records each one as a `Detection`
        with its own evidence -- what was concluded, and from what -- and
        until now not one of them reached a screen. The report carried them
        and the walk did not, so a reader could only find out what this tool
        believed about their project by opening a file it had already
        written.

        Undetermined ones are shown too, and are the reason the status is on
        the page rather than filtered by it: a convention this tool could not
        read is a thing the reader knows about their own project and this
        does not, and hiding those rows would turn "I could not tell" into
        "there is nothing there".
        """
        view = _view()
        if not isinstance(view, SessionView):
            return view
        ctx = read_project(_conversation().project)
        # NOT ctx.project_name: the screen shows it as an identifier, and a
        # project named `warehouse_core` defined "warehouse" here though the
        # word was nowhere in what the page says. See the note in `start`.
        spoken = _spoken(
            (d.label for d in ctx.detections),
            (d.key for d in ctx.detections),
            (d.reading for d in ctx.detections),
            (d.evidence for d in ctx.detections),
            (screen.label for screen in _walk(view) if screen.engine),
        )
        return render_template(
            "project.html",
            screens=_walk(view),
            here="/project",
            teaching=teaching_for(_terms(spoken), _learned(source)),
            project_name=ctx.project_name,
            detections=ctx.detections,
            model_paths=ctx.model_paths,
            layers=ctx.layers,
        )

    @app.get("/")
    def start() -> str | tuple[str, int] | Response:
        view = _view()
        if not isinstance(view, SessionView):
            return view
        renames = _renames(view.change)
        # The consequence in full for one renamed table, and the one-line
        # action for each. A reviewer reading a real three-model report found
        # this paragraph rendered verbatim three times, differing only in two
        # table names, and named it the thing most likely to train a reader
        # to skip the block.
        cutover = next((d for d in renames if d.plain_reason), None)
        # NOT view.change.project_name or str(session.sql): both are
        # identifiers the screen displays rather than prose the engine wrote,
        # marked `data-identifier` in the template for the same reason. A
        # project a reader happens to have named `staging_run`, or a
        # directory they happen to have named `warehouse`, must not define
        # those words on the page as though the conversion's own output used
        # them.
        spoken = _spoken(
            (d.action for d in renames),
            (model.name for model in view.change.models),
            (model.layer for model in view.change.models),
            cutover.reason if cutover else "",
            cutover.plain_reason if cutover else "",
            # The tallies' nouns, which this screen shows beside the counts.
            noun(len(view.change.models), "model"),
            noun(len(renames), "table") if renames else "",
            (screen.label for screen in _walk(view) if screen.engine),
        )
        return render_template(
            "start.html",
            screens=_walk(view),
            here="/",
            teaching=teaching_for(_terms(spoken), _learned(source)),
            project=view.change.project_name,
            sql=source.shown(),
            models=view.change.models,
            renames=renames,
            cutover=cutover,
        )

    @app.post("/mode")
    def mode() -> Response:
        """Switch between the guided walk and the one-screen workbench.

        A POST and not a link, because it changes what this app holds. The
        reader lands back where they pressed it, so switching mode on the
        caveats screen leaves them reading caveats rather than at the start
        of a walk they had already come through.

        Nothing about the conversion moves. Answers, descriptions, tags and
        materializations are all held on the session, and the mode is held
        beside them on `Source` -- so the same conversion is on screen either
        way, which is what the design promises of this button.
        """
        wanted = request.form.get("mode", "")
        if wanted in ("guided", "direct"):
            source.mode = wanted
        back = request.form.get("back", "")
        # Only back to a screen of this walk. A `back` from a form is a value
        # a caller supplies, and following it anywhere would make this an
        # open redirect on somebody's own machine.
        if not back.startswith("/") or back.startswith("//"):
            back = "/"
        return redirect(_counterpart(back))

    def _counterpart(back: str) -> str:
        """The screen of the current mode that matches `back`.

        Most screens are in both modes, and the reader stays on them. The
        asking screens are not: the one screen of direct mode stands for
        every question screen and the describe screen of guided mode. Sent
        back to `/everything` in guided mode, a reader landed on the start,
        because guided mode has no such screen. So the one screen maps to
        the first question -- or to describe, when nothing is asked.

        The refusal page maps to its own way back. After it has rendered, a
        reload of it goes there anyway, and that address was made for the
        mode the answer was given in.
        """
        if back == url_for("refused_page"):
            back = refused_back
        moved = _moved(back)
        if source.mode != "guided" or urlsplit(moved).path != url_for("everything"):
            return moved
        view = _view()
        if not isinstance(view, SessionView) or not view.questions:
            return url_for("describe")
        return url_for("question", index=0)

    def _moved(back: str) -> str:
        """`back`, or the same question's address in the current mode when
        it names a question in either mode's form -- `/questions/<n>` or
        `/everything#question-<n>`, with any query it carries."""
        parts = urlsplit(back)
        index: str | None = None
        if parts.path.startswith("/questions/"):
            index = parts.path.removeprefix("/questions/")
        elif parts.path == url_for("everything") and parts.fragment.startswith("question-"):
            index = parts.fragment.removeprefix("question-")
        if index is None or not index.isdigit():
            return back
        return _at_question(int(index), source.mode, parts.query)

    @app.get("/everything")
    def everything() -> str | tuple[str, int] | Response:
        """Every question and every model on one screen.

        The design's workbench: "same conversion, everything on one screen,
        none of this column." What it replaces is the *asking* -- a screen
        per question and the describe screen -- and nothing else. The screens
        that report what the conversion did are the same in both modes,
        because reading is not the part a comfortable reader wants collapsed.

        In guided mode this is not a screen of the walk, so it sends a reader
        to the start rather than rendering a page the rail does not list.
        """
        if source.mode != "direct":
            return redirect(url_for("start"))
        view = _view()
        if not isinstance(view, SessionView):
            return view
        asks = [
            Asking(
                decision=decision,
                choices=_choices(
                    decision,
                    view.prompts.get(decision.key, {}),
                    _conversation().answers.get(decision.key),
                    _answer_draft(decision.key),
                ),
                index=index,
                example=_example_for(decision, view.change),
                produced=_produced_by(_conversation(), view.change, decision.key),
            )
            for index, decision in enumerate(view.questions)
        ]
        draft = _take_draft()
        models = _describables(view.change, draft)
        stray = _stray(draft, models)
        meanings = [(name, MATERIALIZATION_PLAIN[name]) for name in CHOOSABLE]
        spoken = _spoken(
            (ask.decision.plain_question for ask in asks),
            (ask.decision.question for ask in asks),
            (ask.decision.action for ask in asks),
            (ask.decision.reason for ask in asks),
            (ask.decision.plain_reason for ask in asks),
            (d.action for ask in asks for d in ask.produced),
            (d.reason for ask in asks for d in ask.produced),
            (d.plain_reason for ask in asks for d in ask.produced),
            (c.option.label for ask in asks for c in ask.choices),
            (c.option.effect for ask in asks for c in ask.choices),
            (c.option.plain for ask in asks for c in ask.choices),
            (c.prompt for ask in asks for c in ask.choices),
            (col.name for ask in asks for c in ask.choices for col in c.columns),
            (text for ask in asks for text in _example_text(ask.example)),
            (row.model.name for row in models),
            (name for row in models for name in row.model.depends_on),
            (name for row in models for name in row.model.reads_from),
            (plain for _, plain in meanings),
            draft.message if draft is not None else "",
            # The labels and tally nouns this screen writes around the engine
            # text. A glossary word in one is a word the screen shows.
            "Models",
            noun(len(asks), "question"),
            noun(len(models), "model"),
            (screen.label for screen in _walk(view) if screen.engine),
        )
        page = render_template(
            "everything.html",
            screens=_walk(view),
            here="/everything",
            teaching=teaching_for(_terms(spoken), _learned(source)),
            asks=asks,
            models=models,
            meanings=meanings,
            described=sum(1 for row in models if row.description),
            refusal=stray,
        )
        # 400 while it shows a refused save: the page is the answer to a
        # request the engine refused, reached by the redirect that refusal
        # made, and a reload of it is a fresh look that shows none.
        return (page, 400) if draft is not None else page

    @app.get("/questions/<int:index>")
    def question(index: int) -> str | tuple[str, int] | Response:
        view = _view()
        if not isinstance(view, SessionView):
            return view
        # A question this conversion does not ask is not here, in either
        # mode. Direct mode sent it on to an anchor the one screen does not
        # have, which left the reader at the top of a page with no sign that
        # the address was wrong.
        if not 0 <= index < len(view.questions):
            abort(404)
        # In direct mode there is one place to answer, and this is not it.
        # The route stays reachable rather than 404ing, because a reader who
        # switched mode with a question screen open still has its address --
        # and it lands on that question on the one screen.
        if source.mode == "direct":
            return redirect(_at_question(index, "direct", _query()))
        decision = view.questions[index]
        choices = _choices(
            decision,
            view.prompts.get(decision.key, {}),
            _conversation().answers.get(decision.key),
            _answer_draft(decision.key),
        )
        example = _example_for(decision, view.change)
        produced = _produced_by(_conversation(), view.change, decision.key)
        spoken = _spoken(
            decision.plain_question,
            decision.question,
            decision.action,
            decision.reason,
            decision.plain_reason,
            (d.action for d in produced),
            (d.reason for d in produced),
            (d.plain_reason for d in produced),
            (choice.option.label for choice in choices),
            (choice.option.effect for choice in choices),
            (choice.option.plain for choice in choices),
            (choice.prompt for choice in choices),
            (column.name for choice in choices for column in choice.columns),
            _example_text(example),
            (screen.label for screen in _walk(view) if screen.engine),
        )
        return render_template(
            "question.html",
            screens=_walk(view),
            here=f"/questions/{index}",
            teaching=teaching_for(_terms(spoken), _learned(source)),
            decision=decision,
            choices=choices,
            example=example,
            produced=produced,
        )

    def _describe_page(view: SessionView, draft: Draft | None) -> str:
        """The screen that asks what each model is for.

        The description a reader has written is rendered as an engine run and
        as an identifier, and both halves of that are load-bearing. It is an
        engine run because it came back out of `dbtw.core` -- the text on
        screen is what `change.descriptions` carries, so a template that
        altered a word of it is caught by exact match, the same way a Decision
        is. It is an identifier because it is *theirs*: the glossary on every
        screen is the dbt words that screen uses, and a reader who writes
        "incremental" in a sentence about their own data has not made this
        page use the word -- they have used it, and being handed dbt's
        definition of it back is the tool explaining their own sentence to
        them. The project path on the entry screen is marked for the same
        reason.

        So `spoken` carries the model names and their dependencies and not the
        descriptions. That is not a convenience: `terms_in` is run over the
        page without its identifier regions, so a glossary built from the
        descriptions would define words the check cannot find and the screen
        would fail its own count.

        The exemption is this box, and deliberately not the words wherever
        they go next. Two screens on, the files screen renders the same
        sentence inside the .yml it is about to write, in a `<pre>` that is
        not an identifier region -- so a reader who wrote "dbt build" into a
        description meets its definition there. That is right: on this screen
        they are writing, and on that one they are reading a file, and every
        dbt word in a file this tool is about to put in their project is a
        word that screen shows them. The rule is about who is speaking, not
        about which string it is.

        A refused save comes back as `draft`, for one render: the row it
        names shows the refusal beside it and the reader's typed values in its
        boxes. Those values are the reader's text, so they are identifiers
        here for the same reason a saved description is.
        """
        models = _describables(view.change, draft)
        meanings = [(name, MATERIALIZATION_PLAIN[name]) for name in CHOOSABLE]
        spoken = _spoken(
            (row.model.name for row in models),
            (name for row in models for name in row.model.depends_on),
            (name for row in models for name in row.model.reads_from),
            (plain for _, plain in meanings),
            draft.message if draft is not None else "",
            # The labels and tally nouns this screen writes around the engine
            # text. A glossary word in one is a word the screen shows.
            "Models",
            noun(len(meanings), "choice"),
            (screen.label for screen in _walk(view) if screen.engine),
        )
        return render_template(
            "describe.html",
            screens=_walk(view),
            here="/describe",
            teaching=teaching_for(_terms(spoken), _learned(source)),
            models=models,
            meanings=meanings,
            described=sum(1 for row in models if row.description),
            refusal=_stray(draft, models),
        )

    @app.get("/describe")
    def describe() -> str | tuple[str, int] | Response:
        if source.mode == "direct":
            return redirect(url_for("everything"))
        view = _view()
        if not isinstance(view, SessionView):
            return view
        draft = _take_draft()
        page = _describe_page(view, draft)
        # 400 while it shows a refused save, as the one screen does.
        return (page, 400) if draft is not None else page

    @app.post("/describe")
    def describe_model() -> str | tuple[str, int] | Response:
        """Record what one model is for, and come back to the screen.

        POST/redirect/GET, as the answer loop is, so a refresh re-renders
        rather than re-writing, and the box a reader has just filled comes
        back holding what they wrote -- a screen that showed an empty one
        afterwards gives them no way to tell a saved description from a lost
        one. The redirect goes to the row that was saved, on the screen it
        was saved from: `/describe#model-<name>`, or `/everything#model-<name>`
        in direct mode. A redirect to the top of the screen made the reader
        scroll back down to the row after every save.

        A refusal redirects as well, to the same place, and the screen shows
        it beside the row with what the reader typed still in the boxes. It
        rendered the guided describe screen from this POST before: in direct
        mode that was a screen outside the rail, at an address a reload sent
        again, with the old values in the boxes.

        All three fields or none. `Session.configure` runs the description,
        the tags and the materialization in one conversion and keeps them only
        when it runs, so a refused materialization does not leave the
        description recorded.

        One model per press rather than the whole page at once. A form per
        row is the shape the question screen already uses, and what it buys
        here is that the refusal below is about the row it came from: a
        single form carrying every model would have to pair its boxes to its
        names by position, and a refusal would be about a page rather than
        about a field.

        No gate in front of the session, for the reason `/answer` gives: a
        form arriving without a model names a model this conversion does not
        build, which `Session.configure` refuses in the engine's own words. A
        check here would be a second copy of that rule with a worse message.
        """
        nonlocal drafted
        view = _view()
        if not isinstance(view, SessionView):
            return view
        model = request.form.get("model", "")
        text = request.form.get("text", "")
        tags = request.form.get("tags", "")
        materialization = request.form.get("materialization", "")
        try:
            # The tag box goes to the engine as typed. `clean_tags` is the one
            # place a tag list is split -- on commas and whitespace, blanks
            # dropped, repeats removed -- so "finance, daily finance" is two
            # tags however it was typed.
            _conversation().configure(model, text, (tags,), materialization)
        except (UnknownModelError, ValueError) as refusal:
            drafted = Draft(
                model=model,
                message=str(refusal),
                text=text,
                tags=tags,
                materialization=materialization,
            )
        screen = "everything" if source.mode == "direct" else "describe"
        return redirect(f"{url_for(screen)}#model-{quote(model, safe='')}")

    @app.get("/caveats")
    def caveats() -> str | tuple[str, int] | Response:
        view = _view()
        if not isinstance(view, SessionView):
            return view
        decided = _caveats(view.change, _answered_into_existence(_conversation(), view.change))
        pending = tuple(statement for _, statement in view.change.pending)
        spoken = _spoken(
            (d.action for d in decided),
            (d.reason for d in decided),
            (d.plain_reason for d in decided),
            (d.chosen for d in decided),
            (statement.kind for statement in pending),
            (statement.reason for statement in pending),
            (statement.raw.text for statement in pending),
            (screen.label for screen in _walk(view) if screen.engine),
        )
        return render_template(
            "caveats.html",
            screens=_walk(view),
            here="/caveats",
            teaching=teaching_for(_terms(spoken), _learned(source)),
            caveats=_grouped(decided),
            decided=len(decided),
            pending=pending,
        )

    @app.get("/changed")
    def changed() -> str | tuple[str, int] | Response:
        """Every model beside the SQL it was built from.

        The screen a reader asked for in the words this project keeps quoting
        back at itself: "'Trust me' is exactly the thing I'm not supposed to
        accept before something lands in a repo." The files screen answers
        what will be written; this one answers what it was, which is the only
        question a reader can check against their own memory of their own
        script.

        No extra read: the statements come off the view, out of the same
        conversion that produced the models, so the index a model names and
        the statement at that index cannot have come from two different
        states of the file. See `SessionView.statements`.
        """
        view = _view()
        if not isinstance(view, SessionView):
            return view
        conversions = _conversions(view.change, view.statements)
        # Not the `before` texts. They are an identifier region on this
        # screen -- the reader's own words, displayed and not spoken -- and
        # `terms_in` is run over the page without those, so a glossary built
        # from them would define words the check cannot find. It happens to
        # agree today because everything in a statement reaches the model
        # built from it, comments and grant principals included; leaving it
        # in would make this screen's glossary correct by that coincidence.
        statements = sum(len(row.before) for row in conversions)
        # The tally's nouns too: "3 models, from 4 converted statements" is
        # text this screen shows, so a glossary word in it is a word the
        # screen uses.
        spoken = _spoken(
            (row.after for row in conversions),
            (row.model.name for row in conversions),
            noun(len(conversions), "model"),
            noun(statements, "statement"),
            (screen.label for screen in _walk(view) if screen.engine),
        )
        return render_template(
            "changed.html",
            screens=_walk(view),
            here="/changed",
            teaching=teaching_for(_terms(spoken), _learned(source)),
            conversions=conversions,
            statements=statements,
        )

    @app.get("/files")
    def files() -> str | tuple[str, int] | Response:
        view = _view()
        if not isinstance(view, SessionView):
            return view
        previews = _previews(_conversation(), view.change, _learned(source), source.remember)
        spoken = _spoken(
            (preview.path for preview in previews),
            (preview.contents for preview in previews),
            (screen.label for screen in _walk(view) if screen.engine),
        )
        return render_template(
            "files.html",
            screens=_walk(view),
            here="/files",
            teaching=teaching_for(_terms(spoken), _learned(source)),
            files=previews,
        )

    @app.get("/done")
    def done() -> str | tuple[str, int] | Response:
        view = _view()
        if not isinstance(view, SessionView):
            return view
        # The commands carry their own definitions on this screen -- each one
        # beside what it does, in the order a first run goes -- so the rail's
        # glossary is not rendered here as well. Rendered twice, a reader
        # meets four definitions folded in the rail and the same four open
        # on the canvas, and the folded ones are the ones that look like
        # they might say something different. `terms` stays what the check
        # counts: the definitions on the canvas carry `data-item="term"` and
        # sit in an aside, so this screen defines exactly the words it uses.
        return render_template(
            "done.html",
            screens=_walk(view),
            here="/done",
            teaching=NO_GLOSSARY,
            commands=[(term.name, term.plain) for term in _terms(_spoken(_COMMANDS))],
            out=str(source.out),
        )

    # The latest completed write of this conversation, or None. Held by the
    # app rather than by the session for the reason `create_app` gives about
    # `out`: a conversation is about a conversion, and where its result went
    # is the front end's business.
    written: Written | None = None

    # The latest completed delivery of this conversation, or None, and the
    # write it put on its branch. A second press of a button that has already
    # put a branch in someone's repository must not make another one for the
    # same write, and the walk has no way to undo the first. A new write --
    # made after a changed answer -- is a write no branch holds, so it is
    # offered on a branch of its own: `delivered_of` is how the page tells
    # the two apart.
    delivered: Delivery | None = None
    delivered_of: Written | None = None

    @app.post("/write")
    def write() -> str | tuple[str, int] | Response:
        """Write this conversion to the destination the command line named.

        Terminal, and not a gate: nothing has to be ticked to reach it, and
        every screen of the walk still renders afterwards. Three of three
        personas named the acknowledgment checkbox theatre, and a reader who
        writes and then goes back to read a caveat has to find one.

        Pressed twice with nothing answered in between, it writes once. The
        files on disk are already this conversion's, so a second write would
        be one nobody asked for -- and a reader who double-clicked, or
        refreshed the result, cannot tell one write from two. Answer an
        earlier question differently and the conversion is a different
        conversion, which is a write this has not made, so it makes it.

        A failure renders the error, the destination and the files, and never
        the written screen: spec section 7 asks that a half-written directory
        is never reported as success, and the only way to keep that promise
        is for the page a failed write renders to be a different page.
        """
        nonlocal written
        view = _view()
        if not isinstance(view, SessionView):
            return view
        if written is not None and written.change == view.change:
            return redirect(url_for("written_page"))
        try:
            result = emit(
                view.change,
                read_project(_conversation().project),
                source.out,
                _learned(source),
                sql_path=_conversation().sql,
                remember=source.remember,
            )
        except _WRITE_FAILURES as failure:
            # 500 rather than 400: the form carried nothing wrong. The paths
            # this conversation was started with cannot take the write, and
            # that is the server's side of the exchange to report.
            return _write_failed(view, str(failure)), 500
        written = Written(
            out=source.out,
            files=tuple(path.relative_to(source.out).as_posix() for path in result.paths),
            change=view.change,
            removed=tuple(path.relative_to(source.out).as_posix() for path in result.removed),
        )
        _record(result.paths)
        return redirect(url_for("written_page"))

    @app.get("/written")
    def written_page() -> str | tuple[str, int] | Response:
        """What the write, the delivery and the push did, at an address.

        The three POSTs redirect here -- the shape `/answer` already has --
        so a refresh re-renders rather than asking the browser to send a form
        again, and Back lands on this page rather than on a submission.
        Before anything is written there is nothing to show, so it sends the
        reader to the screen that has the write button -- and so it does
        after a changed answer, when what was written is no longer this
        conversion. That holds after a delivery too: `deliver` checks out
        the reader's own branch again, so the project reads as it did before
        and the conversion moves only when the reader changes it. Shown
        after a changed answer, this page put the old files and the old
        branch in front of a reader as though they were current.
        """
        view = _view()
        if not isinstance(view, SessionView):
            return view
        # One exception: a delivery of this write that could not return the
        # project to its branch. The project then holds the conversion, so
        # the conversion reads differently because of the delivery itself --
        # and this page is the one that says which branch the project is on.
        stranded = delivered is not None and delivered_of is written and delivered.not_returned
        if written is None or (written.change != view.change and not stranded):
            return redirect(url_for("done"))
        return _written_page(view, written)

    def _written_page(
        view: SessionView,
        record: Written,
        *,
        branch: str = "",
        deliver_refusal: str = "",
        push_refusal: str = "",
    ) -> str:
        """What landed, named by the paths `emit` reported writing, and what
        the delivery and the push did with it.

        Project-relative, so the list reads as the same list the files screen
        showed, and read back off `EmitResult.paths` rather than re-derived:
        where a sources file and a per-model schema .yml land are decisions
        `emit` makes, and a second copy of that reasoning here would be free
        to name a file this run did not write.

        A delivery of an earlier write is still shown -- its branch is in
        the reader's repository -- with the engine's sentence that says so,
        and the form to deliver this write beside it. The branch it offers
        is one the repository does not have yet, because `deliver` refuses a
        branch that exists.
        """
        earlier = (
            earlier_write(delivered.branch)
            if delivered is not None and delivered_of is not record
            else ""
        )
        offered = branch or free_branch(_conversation().project, _branch_name(view.change))
        spoken = _spoken(
            str(record.out),
            record.files,
            record.removed,
            earlier,
            delivered.not_returned if delivered is not None else "",
            deliver_refusal,
            push_refusal,
            (screen.label for screen in _walk(view) if screen.engine),
        )
        return render_template(
            "written.html",
            screens=_walk(view),
            here="",
            teaching=teaching_for(_terms(spoken), _learned(source)),
            out=str(record.out),
            files=record.files,
            removed=record.removed,
            branch=offered,
            delivered=delivered,
            earlier=earlier,
            # Only the push of the branch on screen. A push of an earlier
            # delivery is not a push of this one.
            pushed=(
                source.pushed
                if source.pushed is not None
                and delivered is not None
                and source.pushed.branch == delivered.branch
                else None
            ),
            deliver_refusal=deliver_refusal,
            push_refusal=push_refusal,
        )

    @app.post("/deliver")
    def deliver_route() -> str | tuple[str, int] | Response:
        """Put the written conversion onto a branch of the reader's project.

        Offered only after a write, and that ordering is the point: what
        this copies across is the directory the reader has just been shown
        the contents of, file by file. A delivery from a conversion nobody
        has looked at would be this tool writing into their project on the
        strength of its own say-so.

        Every refusal `deliver` makes is about their repository rather than
        their SQL -- not a git repository, a tree with changes in it, a
        branch already taken -- and each is rendered on this screen in the
        engine's own words, with the file list still on it, because a reader
        who has just written a conversion and cannot deliver it needs to know
        that the write still stands.

        One delivery per write. A press for the write a branch already holds
        goes back to the page; a press for a newer write makes a new branch.
        """
        nonlocal delivered, delivered_of
        view = _view()
        if not isinstance(view, SessionView):
            return view
        if written is None:
            # Nothing has been written, so there is nothing to deliver. The
            # button is not on a screen a reader reaches before writing, so
            # this is a form posted out of order rather than a state the walk
            # offers.
            return redirect(url_for("done"))
        if delivered_of is written:
            return redirect(url_for("written_page"))
        branch = request.form.get("branch", "") or free_branch(
            _conversation().project, _branch_name(view.change)
        )
        try:
            # A later delivery starts where the first one started and returns
            # there, whatever the project has checked out now: a first
            # delivery that could not return left the project on its branch.
            delivery = deliver(
                written.out,
                read_project(_conversation().project),
                branch=branch,
                message=_commit_message(view.change),
                start_point=delivered.base if delivered is not None else "",
                return_to=delivered.start if delivered is not None else "",
            )
        except _DELIVER_FAILURES as refusal:
            return _written_page(view, written, branch=branch, deliver_refusal=str(refusal)), 409
        delivered, delivered_of = delivery, written
        return redirect(url_for("written_page"))

    @app.post("/push")
    def push_route() -> str | tuple[str, int] | Response:
        """Send the delivered branch to the reader's own remote.

        Offered only after a delivery, and pressed separately from it. A
        delivery writes inside a directory they already handed this tool; a
        push leaves their machine and is the first thing in this walk that
        anybody else can see. One button doing both would be one press away
        from publishing a conversion nobody had read.

        No credentials of this tool's own: `git push` runs with the reader's
        remote and the reader's keys, so what it can send is exactly what
        they could send from a terminal in that directory.

        Pressed twice, it sends once. The branch is already there and the
        remote would say so, but a second push is a second thing this tool
        did to somebody's server, and the screen already holds what the first
        one said. A newer delivery is a newer branch, which no push has sent.
        """
        view = _view()
        if not isinstance(view, SessionView):
            return view
        if written is None or delivered is None:
            # Nothing has been delivered, so there is no branch to send. The
            # button is not on a screen a reader reaches before delivering,
            # so this is a form posted out of order rather than a state the
            # walk offers.
            return redirect(url_for("done"))
        if source.pushed is not None and source.pushed.branch == delivered.branch:
            return redirect(url_for("written_page"))
        try:
            source.pushed = push(_conversation().project, branch=delivered.branch)
        except _PUSH_FAILURES as refusal:
            return _written_page(view, written, push_refusal=str(refusal)), 409
        return redirect(url_for("written_page"))

    def _write_failed(view: SessionView, message: str) -> str:
        """The refusal in the engine's own words, with the walk around it.

        The files are the previews rather than what reached disk: a write
        that failed part way left some of them there and not others, and a
        list of what landed would read as a result. What the reader needs is
        what this conversion is, so they can fix where it was going and press
        again.
        """
        previews = _previews(_conversation(), view.change, _learned(source), source.remember)
        spoken = _spoken(
            message,
            str(source.out),
            (preview.path for preview in previews),
            (preview.contents for preview in previews),
            (screen.label for screen in _walk(view) if screen.engine),
        )
        return render_template(
            "write_failed.html",
            screens=_walk(view),
            here="",
            teaching=teaching_for(_terms(spoken), _learned(source)),
            message=message,
            out=str(source.out),
            files=previews,
        )

    @app.post("/answer")
    def answer() -> Response | tuple[str, int]:
        """Record one answer and redirect to the question it answered.

        POST/redirect/GET, so a refresh re-renders rather than re-answering,
        and so the reader sees the same screen with the answer applied -- the
        worked example under an append question and under a merge are two
        different tables, and watching one become the other is the teaching.

        Every refusal is the engine's own: an unknown key, a key whose
        Decision asks nothing, a kind that question does not offer, columns an
        option cannot take or lacks. `Session.answer` applies before it
        records, so a refused answer is not held. A refusal redirects too, to
        `/refused`, which renders it with a 400. Rendered from this POST, the
        refusal had no address of its own, and a reload sent the form again.

        In direct mode the answer was given on the one screen, so that is
        where it comes back to, at the question it answered.
        """
        nonlocal refused, refused_back
        # No gate in front of the session. A form arriving without a key or
        # without a kind is refused by `Session.answer` -- an empty key is a
        # key this conversion does not ask, and an empty kind is a kind the
        # question does not offer -- and refused in the engine's own words. A
        # check here would have been a second copy of two rules that already
        # refuse loudly, with a worse message: the only sentence in this
        # module a screen would have shown that no Decision wrote. A mutation
        # deleting the check killed no test, which is what said so.
        key = request.form.get("key", "")
        kind = request.form.get("kind", "")
        ticked = request.form.getlist("columns")
        typed = request.form.get("typed", "")
        columns = _submitted_columns(ticked, typed)
        try:
            _conversation().answer(key, kind, columns)
        except (UnknownAnswerError, ValueError) as refusal:
            # The way back carries the refused answer, so the question it
            # opens has the reader's ticks and typed text in its picker.
            # Without it, Back showed no box ticked and an empty field.
            back = _question_url(_conversation(), key, _draft_query(key, kind, ticked, typed))
            refused = Refused(message=str(refusal), back=back)
            refused_back = refused.back
            return redirect(url_for("refused_page"))

        index = _index_of(_conversation(), key)
        if index is None:
            # Unreachable: an answer this conversion accepted names one of its
            # own questions. Raising rather than defaulting to the first
            # screen, because sending a reader to an answer they did not give
            # is the silence this project does not keep.
            raise UnknownAnswerError(
                f"{key!r} was answered and is not a question of the run that answered it"
            )
        return redirect(_at_question(index, source.mode))

    @app.get("/refused")
    def refused_page() -> str | tuple[str, int] | Response:
        """The refused answer, once, at an address a reload can ask for.

        A reload after it has rendered goes back to the question it refused:
        the refusal page is a report of one press, and what the reader does
        next is answer that question again. With nothing ever refused it goes
        to the start, the screen `_question_url` names for no question.
        """
        shown: Refused | None = g.refused
        if shown is None:
            return redirect(refused_back)
        return _refused(shown), 400

    @app.post("/stale")
    def stale() -> Response:
        """Forget what this script no longer has a place for, and go on.

        Dropping them is a thing that happened, so the screen that offers it
        says which ones -- skipping them silently on the way into a run would
        show a conversion that quietly does not contain an answer the user
        gave.
        """
        session = _conversation()
        # Only what the screen that offered this named, which is what `_stale`
        # chose to render: answers while any are stranded, descriptions once
        # they are not. Dropping both in one press was the first shape of this
        # route and it was wrong -- `stale.html` lists answer keys, so a reader
        # pressing it lost a description that no screen had ever named. Being
        # sent to a second stranded screen is not the cost of that fix, it is
        # the fix: the second screen is where they are told the other half.
        if session.stale_answers():
            session.drop_stale_answers()
        elif session.stale_descriptions():
            session.drop_stale_descriptions()
        elif session.stale_tags():
            session.drop_stale_tags()
        else:
            session.drop_stale_materializations()
        return redirect(url_for("start"))

    def _record(paths: Sequence[Path]) -> None:
        """Note which dbt words this conversion explained, now that it is written.

        After the write and only after it, for the same reason the CLI records
        here: a conversion that failed part way through never claims to have
        explained a word the reader never saw.

        Counted off the report this write produced rather than off the screens
        the reader happened to visit -- the same set the CLI counts, so one
        tool does not retire a word two runs before the other. That under-counts
        a reader who read a screen and skipped the report, which is the safe
        direction: a word stays explained a run longer.
        """
        if not source.remember or source.session is None:
            return
        learned = _learned(source)
        if learned.unreadable:
            return
        report = next((path for path in paths if path.name == REPORT_NAME), None)
        if report is None:
            return
        shown = teaching_for(terms_in(report.read_text(encoding="utf-8")), learned)
        try:
            save_progress(source.session.project, after((*shown.full, *shown.met), learned))
        except (OSError, ValueError) as failure:
            # Never fatal and never silent. The files on disk are correct and
            # complete; what was lost is only that the next conversion repeats
            # these words, and the reader is told in the terminal they started
            # the server from.
            print(
                f"warning: this tool could not record which words it explained: {failure}",
                file=sys.stderr,
            )

    def _refused(shown: Refused) -> str:
        """One refusal, with the walk still around it and a way back into it.

        Not a bare page. Pressing "merge on a unique key" without naming a
        column is the likeliest misstep in the walk, and a first-time user who
        makes it should land on the walk with a sentence on it, not on a page
        with the navigation gone and no route back to the question they were
        answering.
        """
        view = _view()
        screens = _walk(view) if isinstance(view, SessionView) else ()
        return render_template(
            "refused.html",
            screens=screens,
            teaching=teaching_for(_terms(_spoken(shown.message)), _learned(source)),
            here="",
            message=shown.message,
            back=shown.back,
        )

    @app.errorhandler(404)
    def missing(_error: object) -> tuple[str, int]:
        """A page this walk does not have, shaped like the walk.

        Reached by a mistyped address, and by a question index this
        conversion does not ask -- `/questions/9` on a walk with two
        questions, which is one keystroke away from a link that works.
        Werkzeug's stock page for that has no navigation, no way back and
        "check your spelling" on it, which leaves a reader outside the walk
        with nothing on the page to get them back into it.
        """
        view = _view()
        screens = _walk(view) if isinstance(view, SessionView) else ()
        return render_template("missing.html", screens=screens, teaching=NO_GLOSSARY, here=""), 404

    def _question_url(session: Session, key: str, query: str = "") -> str:
        """The screen `key`'s question is on, or the start of the walk.

        Both exits say the same thing: there is a question screen to go back
        to, or there is not. A key this conversion does not ask has none, and
        neither does a conversation that cannot be run at all while a stranded
        answer is held -- and `/` is the screen that says so.

        In direct mode the question is on the one screen, so the way back is
        that screen at the question: its own screen would send the reader
        there anyway, at the top.

        `query` goes on a question's address and not on the start's: it is a
        refused answer to that question, and the start has no picker for it.
        """
        try:
            questions = _conversation().view().questions
        except UnknownAnswerError:
            return url_for("start")
        for index, decision in enumerate(questions):
            if decision.key == key:
                return _at_question(index, source.mode, query)
        return url_for("start")

    def _query() -> str:
        """This request's query string, as it arrived."""
        return request.query_string.decode("utf-8")

    def _answer_draft(key: str) -> tuple[str, tuple[str, ...]] | None:
        """The refused answer to `key` that this request's address carries,
        or None.

        The refusal page links back to the question with the refused answer
        in the address -- see `_draft_query`. An address that names another
        key carries nothing for this question: after an edit to the SQL, an
        old link must not fill a different question's picker.
        """
        if request.args.get("key") != key:
            return None
        return (
            request.args.get("kind", ""),
            _submitted_columns(request.args.getlist("columns"), request.args.get("typed", "")),
        )

    return app


def _example_text(example: Example | None) -> tuple[str, ...]:
    """The words a rendered example puts on the page.

    The three labels travel with it: they are rendered whenever an example
    is, so a word inside one of them is a word that screen uses.
    """
    if example is None:
        return ()
    return (
        *example.columns,
        example.key,
        PLACEHOLDER_NOTICE,
        SUPPOSED_ROW_LABEL,
        AFTER_RUN_LABEL,
    )


def _submitted_columns(ticked: Sequence[str], typed: str) -> tuple[str, ...]:
    """The columns one answer carries: what was ticked, then what was typed.

    Both inputs feed one answer and neither is dropped. A name that arrives
    twice is sent once -- a unique key naming one column twice is not the
    answer anybody gave -- and order is preserved, because a multi-column key
    is ordered.
    """
    columns: list[str] = []
    for name in (*ticked, *typed.split(",")):
        cleaned = name.strip()
        if cleaned and cleaned not in columns:
            columns.append(cleaned)
    return tuple(columns)


def _at_question(index: int, mode: str, query: str = "") -> str:
    """The address of question `index` in `mode`, with `query` on it.

    Guided mode gives each question its own screen. Direct mode puts every
    question on the one screen, so the address is that screen at the
    question's anchor -- and the query goes before the anchor, where a
    browser sends it.
    """
    suffix = f"?{query}" if query else ""
    if mode == "direct":
        return f"/everything{suffix}#question-{index}"
    return f"/questions/{index}{suffix}"


def _draft_query(key: str, kind: str, ticked: Sequence[str], typed: str) -> str:
    """A refused answer, as the query string the way back to its question
    carries.

    In the address and not held on the server. The refusal page renders
    first, and the question renders when the reader presses Back -- so a
    slot handed to the next request, which is how a refused describe save is
    held, would be spent on the refusal page. The address is the way back
    itself: the question it opens shows the draft, and the question opened
    from the rail shows none.

    Bounded, because the address goes in a redirect header: a draft whose
    query is longer than `_DRAFT_LIMIT` is not carried at all, and Back opens
    the question with an empty picker. Cut short instead, the picker would
    show text the reader did not type.
    """
    query = urlencode(
        [("key", key), ("kind", kind), *(("columns", c) for c in ticked), ("typed", typed)]
    )
    return query if len(query) <= _DRAFT_LIMIT else ""


# The longest refused-answer query the way back carries. A key with a long
# file path, a kind, a few ticks and a typed key fit well inside it, and a
# redirect header stays far below what a server accepts.
_DRAFT_LIMIT = 1500


def _index_of(session: Session, key: str) -> int | None:
    """Where the question `key` sits in the walk, or None when this
    conversion asks no such question.

    Read back off the session rather than taken from the form: a redirect
    target the browser supplied is one the browser could get wrong, and the
    index is only meaningful against the run that has just happened.
    """
    for index, decision in enumerate(session.view().questions):
        if decision.key == key:
            return index
    return None


def serve(app: Flask, host: str, port: int, open_browser: bool) -> None:
    """Serve the walk until the user stops it.

    The socket is bound before the address is printed or a browser opened, so
    a port already in use is an error the user reads on their terminal rather
    than a browser window pointed at nothing, and so the address printed is
    the one actually bound -- `--port 0` asks the operating system to choose,
    and a printed 0 would be a lie.

    The address goes to stderr, beside the `note:` lines the convert command
    writes there, so the one line this command prints on stdout stays the one
    line a caller can read.

    `host` is the caller's, and the CLI gives it the loopback address: a
    conversation holds the contents of the user's SQL and their project, and
    a tool that served those to the network by default would be making that
    choice for them.
    """
    # Imported here rather than at module scope only to keep the import list
    # above to what every screen needs; werkzeug is Flask's own dependency.
    from werkzeug.serving import make_server

    server = make_server(host, port, app)
    url = f"http://{host}:{server.server_port}/"
    print(f"Serving the walk at {url} — press Ctrl-C to stop.", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # Ctrl-C is how a local server is stopped, not a failure to report.
        server.server_close()
