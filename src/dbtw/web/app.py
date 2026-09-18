"""The guided walk: six screens over one conversion, and the loop that
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
* it does not register `/write`. The done screen offers the write action,
  because spec section 7 requires a conversion with nothing to answer to
  offer it; the route that performs it is the next task's, and the guard that
  keeps a conversion from being written into the user's own dbt project
  (`cli.main._refuse_output_inside_project`) is reachable from the CLI path
  only. A route that wrote without it would reintroduce, on the surface aimed
  at the people least able to spot it, exactly what that guard exists to
  prevent.
"""

from __future__ import annotations

import sys
import tempfile
import webbrowser
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for
from werkzeug.wrappers.response import Response

from dbtw.core.assemble import ProjectChange, UnknownAnswerError
from dbtw.core.context import read_project
from dbtw.core.emit import (
    AFTER_RUN_LABEL,
    PLACEHOLDER_NOTICE,
    SUPPOSED_ROW_LABEL,
    Example,
    emit,
    worked_example,
)
from dbtw.core.passes import Decision, Option
from dbtw.core.teach import Term, terms_in
from dbtw.web.state import Session, SessionView

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
_COMMANDS = ("dbt compile", "dbt run", "dbt test", "dbt build")


@dataclass(frozen=True, slots=True)
class Screen:
    """One page of the walk, as the nav names it.

    `engine` says whether `label` is a string the engine produced -- a
    question screen is named by the table its question is about -- so the
    template knows whether to mark it.
    """

    url: str
    label: str
    engine: bool


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


@dataclass(frozen=True, slots=True)
class FilePreview:
    """One file this conversion will write, and what will be in it."""

    path: str
    contents: str


def _columns(candidates: Sequence[str]) -> tuple[Column, ...]:
    seen: set[str] = set()
    rows: list[Column] = []
    for name in candidates:
        rows.append(Column(name=name, tick=name not in seen))
        seen.add(name)
    return tuple(rows)


def _choices(decision: Decision, prompts: dict[str, str]) -> tuple[Choice, ...]:
    candidates = decision.subject.candidates if decision.subject is not None else ()
    return tuple(
        Choice(
            option=option,
            chosen=option.label == decision.chosen,
            prompt=prompts.get(option.kind, ""),
            columns=_columns(candidates) if prompts.get(option.kind) else (),
        )
        for option in decision.options
    )


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

    A second `Session` over the same two paths, built through the public
    constructor. It is not a second code path: the same pipeline runs, on the
    same inputs, with a different answer set, which is the only way to ask
    what one answer is responsible for. `answers` is copied rather than
    shared, so nothing this asks can change what the session holds.
    """
    held = {key: value for key, value in session.answers.items() if key != without}
    other = Session(
        project=session.project,
        sql=session.sql,
        dialect=session.dialect,
        answers=held if without else {},
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


def _previews(session: Session, change: ProjectChange) -> tuple[FilePreview, ...]:
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
        result = emit(change, read_project(session.project), out)
        return tuple(
            FilePreview(
                path=path.relative_to(out).as_posix(),
                contents=path.read_text(encoding="utf-8"),
            )
            for path in result.paths
        )


def _screens(view: SessionView) -> tuple[Screen, ...]:
    """The walk, in order. One screen per question, and four fixed ones."""
    questions = [
        Screen(
            url=f"/questions/{index}",
            label=decision.subject.table if decision.subject else decision.key,
            engine=True,
        )
        for index, decision in enumerate(view.questions)
    ]
    return (
        Screen(url="/", label="Start", engine=False),
        *questions,
        Screen(url="/caveats", label="Decided for you", engine=False),
        Screen(url="/files", label="Files", engine=False),
        Screen(url="/done", label="Done", engine=False),
    )


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


def create_app(session: Session) -> Flask:
    """The walk over one conversation. One session, held by the app.

    Keyed by nothing, as spec 4.1 requires: this is a local single-user tool
    and a second tab is a second view of the same conversation.
    """
    app = Flask(__name__)
    # The three strings a rendered example is shown under. `emit.example`
    # owns them because each is a claim only that module can vouch for --
    # that the values are placeholders, that the first block is a premise
    # rather than a row this engine read, and that what acts on it is this
    # model rather than the reader's script. A template writing its own
    # wording for any of them would be authoring the claim.
    app.jinja_env.globals.update(
        example_notice=PLACEHOLDER_NOTICE,
        supposed_label=SUPPOSED_ROW_LABEL,
        after_label=AFTER_RUN_LABEL,
    )

    def _stale() -> tuple[str, int] | None:
        """The screen for the one state in which nothing else can render.

        Every accessor raises while an answer is held whose question the
        current SQL no longer asks -- the script was edited underneath it.
        `stale_answers` is the one that still answers, so the screen can say
        which answers it is about and offer the way out, instead of showing
        an error with no subject and no way on. It is rendered only in that
        state, so an edge case never becomes the page's main event.
        """
        stale = session.stale_answers()
        if not stale:
            return None
        return render_template("stale.html", stale=stale, screens=(), terms=(), here=""), 409

    def _view() -> SessionView | tuple[str, int]:
        try:
            return session.view()
        except UnknownAnswerError:
            stranded = _stale()
            if stranded is None:
                raise
            return stranded

    @app.get("/")
    def start() -> str | tuple[str, int]:
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
        spoken = _spoken(
            view.change.project_name,
            str(session.sql),
            (d.action for d in renames),
            cutover.reason if cutover else "",
            cutover.plain_reason if cutover else "",
            (screen.label for screen in _screens(view) if screen.engine),
        )
        return render_template(
            "start.html",
            screens=_screens(view),
            here="/",
            terms=_terms(spoken),
            project=view.change.project_name,
            sql=str(session.sql),
            renames=renames,
            cutover=cutover,
        )

    @app.get("/questions/<int:index>")
    def question(index: int) -> str | tuple[str, int]:
        view = _view()
        if not isinstance(view, SessionView):
            return view
        if not 0 <= index < len(view.questions):
            abort(404)
        decision = view.questions[index]
        choices = _choices(decision, view.prompts.get(decision.key, {}))
        example = _example_for(decision, view.change)
        produced = _produced_by(session, view.change, decision.key)
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
            (screen.label for screen in _screens(view) if screen.engine),
        )
        return render_template(
            "question.html",
            screens=_screens(view),
            here=f"/questions/{index}",
            terms=_terms(spoken),
            decision=decision,
            choices=choices,
            example=example,
            produced=produced,
        )

    @app.get("/caveats")
    def caveats() -> str | tuple[str, int]:
        view = _view()
        if not isinstance(view, SessionView):
            return view
        decided = _caveats(view.change, _answered_into_existence(session, view.change))
        pending = tuple(statement for _, statement in view.change.pending)
        spoken = _spoken(
            (d.action for d in decided),
            (d.reason for d in decided),
            (d.plain_reason for d in decided),
            (d.chosen for d in decided),
            (statement.kind for statement in pending),
            (statement.reason for statement in pending),
            (statement.raw.text for statement in pending),
            (screen.label for screen in _screens(view) if screen.engine),
        )
        return render_template(
            "caveats.html",
            screens=_screens(view),
            here="/caveats",
            terms=_terms(spoken),
            caveats=_grouped(decided),
            decided=len(decided),
            pending=pending,
        )

    @app.get("/files")
    def files() -> str | tuple[str, int]:
        view = _view()
        if not isinstance(view, SessionView):
            return view
        previews = _previews(session, view.change)
        spoken = _spoken(
            (preview.path for preview in previews),
            (preview.contents for preview in previews),
            (screen.label for screen in _screens(view) if screen.engine),
        )
        return render_template(
            "files.html",
            screens=_screens(view),
            here="/files",
            terms=_terms(spoken),
            files=previews,
        )

    @app.get("/done")
    def done() -> str | tuple[str, int]:
        view = _view()
        if not isinstance(view, SessionView):
            return view
        spoken = _spoken(
            _COMMANDS,
            (screen.label for screen in _screens(view) if screen.engine),
        )
        return render_template(
            "done.html",
            screens=_screens(view),
            here="/done",
            terms=_terms(spoken),
            commands=_COMMANDS,
        )

    @app.post("/answer")
    def answer() -> Response | tuple[str, int]:
        """Record one answer and redirect to the question it answered.

        POST/redirect/GET, so a refresh re-renders rather than re-answering,
        and so the reader sees the same screen with the answer applied -- the
        worked example under an append question and under a merge are two
        different tables, and watching one become the other is the teaching.

        Every refusal is the engine's own, rendered with a 400: an unknown
        key, a key whose Decision asks nothing, a kind that question does not
        offer, columns an option cannot take or lacks. `Session.answer`
        applies before it records, so a refused answer is not held.
        """
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
        columns = _submitted_columns(request.form.getlist("columns"), request.form.get("typed", ""))
        try:
            session.answer(key, kind, columns)
        except (UnknownAnswerError, ValueError) as refusal:
            return _refused(str(refusal), key), 400

        index = _index_of(session, key)
        if index is None:
            # Unreachable: an answer this conversion accepted names one of its
            # own questions. Raising rather than defaulting to the first
            # screen, because sending a reader to an answer they did not give
            # is the silence this project does not keep.
            raise UnknownAnswerError(
                f"{key!r} was answered and is not a question of the run that answered it"
            )
        return redirect(url_for("question", index=index))

    @app.post("/stale")
    def stale() -> Response:
        """Forget the answers this script no longer asks for, and go on.

        Dropping them is a thing that happened, so the screen that offers it
        says which ones -- skipping them silently on the way into a run would
        show a conversion that quietly does not contain an answer the user
        gave.
        """
        session.drop_stale_answers()
        return redirect(url_for("start"))

    def _refused(message: str, key: str) -> str:
        """One refusal, with the walk still around it and a way back into it.

        Not a bare page. Pressing "merge on a unique key" without naming a
        column is the likeliest misstep in the walk, and a first-time user who
        makes it should land on the walk with a sentence on it, not on a page
        with the navigation gone and no route back to the question they were
        answering.
        """
        view = _view()
        screens = _screens(view) if isinstance(view, SessionView) else ()
        return render_template(
            "refused.html",
            screens=screens,
            terms=(),
            here="",
            message=message,
            back=_question_url(session, key),
        )

    @app.errorhandler(404)
    def missing(_error: object) -> tuple[str, int]:
        """A page this walk does not have, shaped like the walk.

        `/write` is the one a reader reaches by pressing a button: the done
        screen offers the write action because spec section 7 requires it,
        and the route that performs it is not served by this build. Werkzeug's
        stock page for that -- no navigation, no way back, "check your
        spelling" -- makes the terminal action of the walk look like an error,
        which is the one thing section 7 says it must not look like.
        """
        view = _view()
        screens = _screens(view) if isinstance(view, SessionView) else ()
        return render_template("missing.html", screens=screens, terms=(), here=""), 404

    def _question_url(session: Session, key: str) -> str:
        """The screen `key`'s question is on, or the start of the walk.

        Both exits say the same thing: there is a question screen to go back
        to, or there is not. A key this conversion does not ask has none, and
        neither does a conversation that cannot be run at all while a stranded
        answer is held -- and `/` is the screen that says so.
        """
        try:
            questions = session.view().questions
        except UnknownAnswerError:
            return url_for("start")
        for index, decision in enumerate(questions):
            if decision.key == key:
                return url_for("question", index=index)
        return url_for("start")

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
