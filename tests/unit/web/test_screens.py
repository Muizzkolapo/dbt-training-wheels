"""What the rendered screens may and may not say.

The measurable claims from spec sections 6.1, 7 and 11: the web layer authors
no explanatory text, every number is the length of what is listed beside it,
nothing the tool decided is shown as something the user chose, and no gate
demands a certification the page gives no way to obtain.

`tests/unit/web/page.py` reads a screen back into engine runs and authored
runs; `tests/unit/web/engine_strings.py` gathers, from `dbtw.core` alone,
every string this conversion produced. Neither reads a template.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.web.conftest import Walk
from tests.unit.web.engine_strings import engine_strings, written_files
from tests.unit.web.helpers import (
    DUPLICATE_CANDIDATES,
    ONE_APPEND,
    ONE_APPEND_ELSEWHERE,
    ONE_MERGE,
    STAR_PROJECTION,
)
from tests.unit.web.page import Page, normalised, read, words

import dbtw.web
from dbtw.core.context import read_project
from dbtw.core.emit import (
    AFTER_RUN_LABEL,
    PLACEHOLDER_NOTICE,
    SUPPOSED_ROW_LABEL,
    emit,
    worked_example,
)
from dbtw.core.teach import terms_in
from dbtw.web import Session, Source
from dbtw.web.app import _branch_name, _described_state, _screens

# What counts as a sentence in an authored run. Every label the templates
# write is three words or fewer ("In plain words", "Write these files"), so
# five is comfortably above the longest of them and comfortably below any
# sentence that explains something. It is deliberately low: this test exists
# to fail the moment a template starts explaining.
_SENTENCE = 5


# The entry screen. Not a screen of the walk: it is how a reader gets one,
# and it renders before there is a conversion to render. Every guard that
# asks something of "a screen of the walk" reaches it through
# `_guarded_pages` rather than through `_screen_urls`, so it is held to all
# of them -- including the prose rule, which it passes: it explains no
# Decision, so it has nothing to explain at sentence length, and every run it
# writes is a label. It is exempt from exactly one assertion, that a page
# shows a derived count, because it has nothing to count.
_ENTRY = "/source"


def _screen_urls(app, session: Session) -> tuple[str, ...]:
    """Every screen of the walk, derived from the app's own routing table.

    Not a list written here. A per-screen test given a hand-kept list covers
    whatever the list happens to name, and the walk grew a screen the day
    somebody added a route. Each GET rule contributes one URL, except the
    question rule, which contributes one per question this conversion asks.
    A rule taking any other argument stops the test rather than being skipped
    -- a screen this cannot address is a screen nothing here checks.

    `/source` is excluded, and it is the only exclusion: it is the screen a
    reader is on *before* this walk exists, so it is not one of the walk's
    own, does not carry the walk's navigation, and has no conversation to
    render. It is still guarded -- see `_ENTRY`.
    """
    asked = len(session.view().questions)
    urls: list[str] = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda rule: rule.rule):
        methods = rule.methods or set()
        if rule.endpoint == "static" or "GET" not in methods or rule.rule == _ENTRY:
            continue
        if not rule.arguments:
            urls.append(rule.rule)
        elif rule.arguments == {"index"}:
            urls.extend(rule.rule.replace("<int:index>", str(i)) for i in range(asked))
        else:
            raise AssertionError(
                f"{rule.rule} takes {sorted(rule.arguments)}, which this test cannot "
                "address; a screen it cannot reach is a screen it does not check"
            )
    return tuple(urls)


def _engine(page: Page) -> set[str]:
    """Every run the page claims from the engine, whitespace-normalised.

    Both sides of every comparison go through `normalised`: a `<pre>` keeps
    the newlines a model body was written with, and a `<span>` sits on an
    indented template line.
    """
    return {normalised(run) for run in page.engine}


# The states a screen of this walk is rendered in. Every cross-cutting guard
# runs in all of them, because most of the walk's screens change under the
# answer loop and a guard evaluated only before the first answer is a guard
# evaluated in the one state where it cannot fail. "downgraded" is the answer the engine
# accepts and then does not apply -- a key the model does not select -- which
# is the state that puts a Decision on the page that no pristine run carries.
#
# The last two are the states a reader is in once they have pressed the one
# control in the walk that touches their disk. They are here because the
# write screens are screens: a guard that only ever ran over GET-served pages
# on a pristine walk is how ten of the last review's twelve survivors
# survived.
_PRISTINE = "pristine"
_ANSWERED = "answered"
_DOWNGRADED = "downgraded"
_STALE = "stale"
# The same edit, under the other thing a reader holds. `stale.html` has had
# every cross-cutting guard run over it since it was written; its sibling
# would have had none, which is the shape of the unguarded-screen defect this
# file has already been fixed for twice.
_STALE_DESCRIPTION = "stale description"
_WRITTEN = "written"
_WRITE_FAILED = "write failed"
# The two states a reader reaches by pressing the second button that touches
# their disk. Delivery puts a branch in their own repository, so the screen
# that reports it and the screen that refuses it are both pages a reader
# lands on -- and a guard that never visited them would be the same
# wrong-coverage-set defect this file has already been fixed for twice.
_DELIVERED = "delivered"
_DELIVERY_REFUSED = "delivery refused"
# The walk with a description written into it. The describe screen renders
# nothing of the reader's until they have written one, and neither does the
# .yml on the files screen -- so a guard that only ever met a blank walk met
# the one state in which this screen has nothing on it to be wrong about,
# which is how sixteen wrong-coverage-set defects have been found in this
# file and in the guards around it.
_DESCRIBED = "described"
_STATES = (
    _PRISTINE,
    _ANSWERED,
    _DOWNGRADED,
    _DESCRIBED,
    _STALE,
    _STALE_DESCRIPTION,
    _WRITTEN,
    _WRITE_FAILED,
    _DELIVERED,
    _DELIVERY_REFUSED,
)

# What a reader writes on the describe screen in the state built around it.
# Long enough to be a sentence by `_SENTENCE`, because that is the point: a
# description short enough to pass the prose threshold on its own would leave
# the threshold untested against the one text on these screens that is
# neither the engine's nor a template's.
#
# And it uses a glossary word. A reader who writes "incremental" in a
# sentence about their own data has not made the screen use the word, and the
# screen must not answer them with dbt's definition of it -- so the box is
# marked as an identifier region and the glossary is built without it. With a
# description that happened to contain no dbt word, that marking would be
# unfalsifiable: every state would pass with it and without it.
_DESCRIPTION = "One row per revenue event, at the grain the incremental load writes it."


def _append_key(session: Session) -> str:
    return next(d.key for d in session.view().questions if ".append." in d.key)


def _walk_in(walk: Walk, sql_script, walk_sql: Path, state: str, project_dir: Path, out_dir: Path):  # type: ignore[no-untyped-def]
    """The walk in one of the six states: the app, a client, the session, and
    the destination that walk writes to.

    The destination is returned rather than assumed by the caller: one state
    is built around a destination that refuses the write, and a caller that
    took `out_dir` for granted would check the failure page against the
    strings of a directory it was never aimed at.
    """
    if state == _STALE:
        sql = sql_script(ONE_APPEND_ELSEWHERE)
        app, client, session = walk(sql)
        (question,) = session.view().questions
        assert (
            client.post("/answer", data={"key": question.key, "kind": "append"}).status_code == 302
        )
        sql.write_text(ONE_MERGE, encoding="utf-8")
        return app, client, session, out_dir

    if state == _STALE_DESCRIPTION:
        sql = sql_script(ONE_APPEND_ELSEWHERE)
        app, client, session = walk(sql)
        (model,) = session.view().change.models
        described = client.post("/describe", data={"model": model.name, "text": _DESCRIPTION})
        assert described.status_code == 302
        sql.write_text(ONE_MERGE, encoding="utf-8")
        return app, client, session, out_dir

    if state == _WRITE_FAILED:
        # Aimed at the project itself, which `emit` refuses. The walk is
        # then in the state a reader reaches by pressing write with a
        # destination that cannot take it, and every screen still has to
        # render around that.
        app, client, session = walk(walk_sql, out=project_dir)
        assert client.post("/write").status_code == 500
        return app, client, session, project_dir

    if state in (_DELIVERED, _DELIVERY_REFUSED):
        # Delivery is offered only after a write, so both states are a
        # written walk plus one press. The difference is the project: a git
        # repository with a clean tree accepts the branch, and a directory
        # that is not a repository at all is refused -- the commonest of the
        # three refusals, and the one a reader meets without having done
        # anything wrong.
        if state == _DELIVERED:
            _as_git_repo(project_dir)
        app, client, session = walk(walk_sql)
        assert client.post("/write").status_code == 200
        delivered = client.post("/deliver")
        assert delivered.status_code == (200 if state == _DELIVERED else 409)
        return app, client, session, out_dir

    app, client, session = walk(walk_sql)
    if state == _DESCRIBED:
        described = client.post(
            "/describe",
            data={"model": session.view().change.models[0].name, "text": _DESCRIPTION},
        )
        assert described.status_code == 302
    elif state == _ANSWERED:
        answer = {"key": _append_key(session), "kind": "merge_checked", "typed": "event_id"}
        assert client.post("/answer", data=answer).status_code == 302
    elif state == _DOWNGRADED:
        answer = {"key": _append_key(session), "kind": "merge", "typed": "evnt_id"}
        assert client.post("/answer", data=answer).status_code == 302
    elif state == _WRITTEN:
        assert client.post("/write").status_code == 200
    return app, client, session, out_dir


def _pristine_decisions(session: Session):  # type: ignore[no-untyped-def]
    """This conversion's Decisions with no answers in it.

    A second `Session` over the same two paths, through the public
    constructor. What an answer produced is what this run does not carry.
    """
    return (
        Session(project=session.project, sql=session.sql, dialect=session.dialect)
        .view()
        .change.decisions
    )


# A model name no conversion here builds. Pressed on the describe screen in
# every state, so the page that refuses a description is inside the coverage
# set of every cross-cutting guard -- the answer refusal has been since it was
# written, and a second refusal page outside it is the same
# wrong-coverage-set defect wearing a different route.
_NOT_A_MODEL = "not_a_model_this_conversion_builds"


def _description_refusal(session: Session) -> str:
    """What the engine says when a description names a model nothing builds.

    Asked of the engine by making the call, for the reason `_refusal_message`
    gives. `Session.describe` restores what it held when a run refuses, so
    asking costs the session nothing.
    """
    try:
        session.describe(_NOT_A_MODEL, "a description of nothing")
    except ValueError as refusal:
        return str(refusal)
    raise AssertionError("a description naming no model was accepted")


def _refusal_message(session: Session) -> str:
    """What the engine says when the merge answer arrives with no columns.

    Asked of the engine by making the call, not written out here: the refusal
    page renders the exception's own sentence, and a test that spelled that
    sentence out would be checking the page against a copy of the message
    instead of against the message. `Session.answer` restores what it held
    when a run refuses, so asking costs the session nothing.
    """
    try:
        session.answer(_append_key(session), "merge", ())
    except ValueError as refusal:
        return str(refusal)
    raise AssertionError("the merge answer with no columns was accepted")


def _produced(session: Session, out: Path, state: str) -> frozenset[str]:
    """Every string the engine produces for this state, both refusals included.

    The two refusals are asked of the engine by *making the call*, never
    written out here: each page renders the exception's own sentence, and a
    test that spelled one out would be checking the page against a copy of
    the message instead of against the message.
    """
    strings = engine_strings(session, out)
    if state in (_STALE, _STALE_DESCRIPTION):
        return strings
    # Every state but the stranded ones renders both refusals, because
    # `_guarded_pages` presses the misstep that produces each.
    strings |= {normalised(_refusal_message(session))}
    strings |= {normalised(_description_refusal(session))}
    if state == _WRITE_FAILED:
        strings |= {normalised(_write_refusal(session, out))}
    if state in (_DELIVERED, _DELIVERY_REFUSED):
        # What a delivery reports is git's own: a branch name this module
        # derived and a commit sha only git can know. Asked of the thing that
        # produced them rather than written out here, for the reason
        # `_refusal_message` gives.
        strings |= _delivery_strings(session, out, state)
    return strings


def _write_refusal(session: Session, out: Path) -> str:
    """What `emit` says when this conversion is aimed at `out`."""
    try:
        emit(session.view().change, read_project(session.project), out)
    except ValueError as refusal:
        return str(refusal)
    raise AssertionError(f"emit accepted {out} as a destination")


def _as_git_repo(root: Path) -> None:
    """Make `root` a committed git repository, so a delivery can branch it.

    Identity is set on the repository rather than read from the machine: a
    test that depended on whoever runs it having git configured would fail on
    a fresh checkout for a reason that has nothing to do with this code.
    """
    import subprocess

    def git(*arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=root, capture_output=True, check=True)

    git("init", "-q")
    git("config", "user.email", "reader@example.invalid")
    git("config", "user.name", "The Reader")
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts):
        git("add", "--", str(path.relative_to(root)))
    git("commit", "-q", "-m", "the project as it was")


def _delivery_strings(session: Session, out: Path, state: str) -> frozenset[str]:
    """What the delivered or refused screen renders that only git can produce.

    The two states are asked differently, and they have to be. A refusal is
    deterministic, so it is obtained by *making the call* against a throwaway
    copy of the project -- the same discipline `_refusal_message` uses, and
    safe because a refused delivery changes nothing. A commit sha is not
    deterministic: every commit has its own, so re-delivering somewhere else
    produces a different one and could never match the page. That one is read
    back out of the repository the delivery actually went into, which is
    asking the thing that produced it in the most direct way available.
    """
    import shutil
    import subprocess
    import tempfile

    from dbtw.core.deliver import deliver

    if state == _DELIVERED:

        def git(*arguments: str) -> str:
            done = subprocess.run(
                ["git", *arguments], cwd=session.project, capture_output=True, text=True, check=True
            )
            return done.stdout.strip()

        return frozenset(
            {normalised(git("rev-parse", "HEAD")), normalised(git("branch", "--show-current"))}
        )

    scratch = Path(tempfile.mkdtemp()) / "project"
    shutil.copytree(session.project, scratch)
    branch = _branch_name(session.view().change)
    try:
        deliver(out, read_project(scratch), branch=branch, message="m")
    except ValueError as refusal:
        return frozenset({normalised(str(refusal)), normalised(branch)})
    raise AssertionError("the delivery this state is built around was accepted")


def _guarded_pages(app, client, session: Session, state: str) -> dict[str, Page]:
    """Every page a reader can land on in this state, screens and non-screens
    alike.

    `/answer`, `/describe`, `/stale` and `/write` are POST-only or POST as
    well, so a screen list derived from the routing table filters them out.
    Three of the pages they render are pages a reader really lands on: the
    answer refusal, because pressing "merge on a unique key" without naming a
    column lands on it; the describe refusal, which a reader reaches by
    holding a describe form open across a change to their SQL; and the write
    result, because it is the last thing the walk shows. A guard that never
    visited those is a guard that never visited the pages it matters most on
    -- and the describe refusal was outside this set for exactly as long as
    it took a reviewer to plant a nineteen-word invented sentence on it and
    watch the whole suite pass.
    """
    if state in (_STALE, _STALE_DESCRIPTION):
        stranded = client.get("/")
        assert stranded.status_code == 409
        written = client.post("/write")
        assert written.status_code == 409
        return {
            "/ (stranded)": read(stranded.get_data(as_text=True)),
            "/write (stranded)": read(written.get_data(as_text=True)),
        }

    pages = _pages(app, client, session)
    refusal = client.post("/answer", data={"key": _append_key(session), "kind": "merge"})
    assert refusal.status_code == 400
    pages["/answer (refused)"] = read(refusal.get_data(as_text=True))
    described = client.post("/describe", data={"model": _NOT_A_MODEL, "text": "words"})
    assert described.status_code == 400
    pages["/describe (refused)"] = read(described.get_data(as_text=True))
    result = client.post("/write")
    assert result.status_code == (500 if state == _WRITE_FAILED else 200)
    pages["/write"] = read(result.get_data(as_text=True))
    # The entry screen is not one of the walk's screens, and is guarded like
    # one anyway: a number on it still has to be the length of what it lists,
    # a heading still has to open something, and it still may not gate the
    # write action behind a tick. The one rule it is exempt from is the prose
    # rule, and that exemption is named where it is taken.
    entry = client.get(_ENTRY)
    assert entry.status_code == 200
    pages[_ENTRY] = read(entry.get_data(as_text=True))
    return pages


def _pages(app, client, session: Session) -> dict[str, Page]:
    pages: dict[str, Page] = {}
    for url in _screen_urls(app, session):
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        pages[url] = read(response.get_data(as_text=True))
    return pages


def test_the_walk_is_the_nine_screens_its_own_routes_define(walk: Walk, walk_sql: Path) -> None:
    """The conversion the ten walkthroughs were run against asks two
    questions, so the walk is nine screens: your project, start, two
    questions, describe, caveats, what changed, files, done.
    """
    app, client, session = walk(walk_sql)

    urls = _screen_urls(app, session)

    assert urls == (
        "/",
        "/caveats",
        "/changed",
        "/describe",
        "/done",
        "/files",
        "/project",
        "/questions/0",
        "/questions/1",
    )
    assert len(urls) == 9
    for url in urls:
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize("state", _STATES)
def test_no_screen_authors_a_sentence(
    walk: Walk, sql_script, walk_sql: Path, project_dir: Path, out_dir: Path, state: str
) -> None:
    """Spec section 6.1, mechanically.

    Two assertions, and they are one claim seen from both ends. A template
    that wrote a sentence of its own fails the second; a template that wrote
    one and marked it as the engine's fails the first, because the mark is
    checked by exact match against what `dbtw.core` actually produced.

    In every state, because most of the walk's screens change between them.
    Run only against a pristine walk, the first
    assertion held for a reason that had nothing to do with the templates:
    the page renders the *pristine* question's `columns_prompt` -- which is
    the design, and the only honest source for it -- while the set it was
    checked against was gathered from the *answered* run, where that field is
    empty. So the one state it was evaluated in was the one state where the
    two runs agree.
    """
    app, client, session, out = _walk_in(walk, sql_script, walk_sql, state, project_dir, out_dir)
    produced = _produced(session, out, state)

    for url, page in _guarded_pages(app, client, session, state).items():
        unclaimed = [run for run in page.engine if normalised(run) not in produced]
        assert not unclaimed, f"{state} {url} marks text the engine never produced: {unclaimed[:2]}"

        authored = page.sentences(_SENTENCE)
        assert not authored, f"{state} {url} authors prose: {authored}"


def test_the_prose_check_sees_a_sentence_a_template_would_have_written(
    walk: Walk, walk_sql: Path, out_dir: Path
) -> None:
    """The check above, shown failing. A clean page proves nothing about a
    detector that cannot fire: this plants the two sentences the mutation
    round plants for real -- a plain one, and one built only from words the
    engine already uses on that page, which is the version a stripper that
    subtracted engine strings from the page text would have erased along with
    them.
    """
    app, client, session = walk(walk_sql)
    body = client.get("/questions/1").get_data(as_text=True)

    plain = "<p>This screen shows you what the conversion will do next.</p>"
    borrowed = (
        "<p>Every run re-inserts everything this model selects, so rows "
        "already in the table stay where they are.</p>"
    )
    marked = "<p data-engine>This screen shows you what the conversion will do next.</p>"

    assert read(body).sentences(_SENTENCE) == ()
    assert read(body.replace("</main>", plain + "</main>")).sentences(_SENTENCE)
    assert read(body.replace("</main>", borrowed + "</main>")).sentences(_SENTENCE)

    # And the marker buys nothing: claimed as the engine's, the same sentence
    # is no longer measured as authored, and is caught by the other assertion.
    salted = read(body.replace("</main>", marked + "</main>"))
    produced = engine_strings(session, out_dir)
    assert [run for run in salted.engine if normalised(run) not in produced]


@pytest.mark.parametrize("state", _STATES)
def test_the_prose_check_has_something_left_to_look_at(
    walk: Walk, sql_script, walk_sql: Path, project_dir: Path, out_dir: Path, state: str
) -> None:
    """Every page leaves authored text behind after the engine's is set
    aside -- headings, nav, buttons. A page whose authored side came back
    empty would pass the check above having inspected nothing.
    """
    app, client, session, out = _walk_in(walk, sql_script, walk_sql, state, project_dir, out_dir)
    for url, page in _guarded_pages(app, client, session, state).items():
        assert page.authored, f"{state} {url} leaves nothing authored to inspect"
        assert page.engine, f"{state} {url} renders nothing from the engine"


def test_every_question_screen_shows_the_engine_s_example_or_no_example_at_all(
    walk: Walk, walk_sql: Path
) -> None:
    """Section 11.2: every question needs a worked example, and where
    `worked_example` refuses one -- this conversion's merge question is over
    a `SELECT *`, whose columns are not knowable -- the screen says nothing in
    its place. Both cases occur here, which is why this conversion is the one
    the test runs over.
    """
    app, client, session = walk(walk_sql)
    view = session.view()
    refused = 0

    for index, decision in enumerate(view.questions):
        page = read(client.get(f"/questions/{index}").get_data(as_text=True))
        example = next(
            (
                built
                for model in view.change.models
                if (built := worked_example(decision, model, view.change.dialect)) is not None
            ),
            None,
        )
        rendered = _engine(page)
        if example is None:
            refused += 1
            furniture = {PLACEHOLDER_NOTICE, SUPPOSED_ROW_LABEL, AFTER_RUN_LABEL}
            assert not rendered & furniture, (
                f"question {index} shows example furniture with no example in it"
            )
            continue
        for column in example.columns:
            assert column in rendered, f"question {index} drops the column {column}"
        for row in (*example.before, *example.model_after):
            for cell in row:
                assert cell in rendered, f"question {index} drops the cell {cell}"

    assert refused == 1, "this conversion is meant to carry one question an example refuses"


@pytest.mark.parametrize("state", _STATES)
def test_no_screen_renders_a_heading_with_an_empty_body(
    walk: Walk, sql_script, walk_sql: Path, project_dir: Path, out_dir: Path, state: str
) -> None:
    app, client, session, out = _walk_in(walk, sql_script, walk_sql, state, project_dir, out_dir)
    for url, page in _guarded_pages(app, client, session, state).items():
        assert page.empty_headings() == (), f"{state} {url} opens a section and says nothing in it"


def test_the_empty_heading_check_catches_one(walk: Walk, walk_sql: Path) -> None:
    app, client, session = walk(walk_sql)
    body = client.get("/files").get_data(as_text=True)
    salted = body.replace("</main>", "<h2>Orphan</h2></main>")
    assert read(salted).empty_headings() == ("Orphan",)


@pytest.mark.parametrize("state", _STATES)
def test_every_count_is_the_length_of_what_is_listed_beside_it(
    walk: Walk, sql_script, walk_sql: Path, project_dir: Path, out_dir: Path, state: str
) -> None:
    """Section 11.4(c), both halves.

    A declared number is held against the list beside it -- the failure that
    cost the most trust was "4 new files" over a list of five. But checking
    only the numbers that declare themselves checks nothing about a number
    that does not: written as a bare `<p>4 new files</p>`, the exact string
    from the finding is two words and one digit, so the prose threshold
    cannot see it and the count loop never looks at it. So every digit in
    every run the templates wrote has to sit inside a `data-count` element,
    and that is the second assertion.
    """
    app, client, session, out = _walk_in(walk, sql_script, walk_sql, state, project_dir, out_dir)
    seen = 0
    for url, page in _guarded_pages(app, client, session, state).items():
        # Every page of the walk carries at least the nav's own screen count.
        # The entry screen carries no nav, because the walk it would navigate
        # does not exist yet -- so it is the one page with nothing to count,
        # and "counts nothing" is the honest rendering rather than a zero.
        # Both assertions below still apply to it: what it counts must match,
        # and a digit outside a count is still a number answering to nothing.
        if url != _ENTRY:
            assert page.counts, f"{state} {url} shows no derived count"
        for name, shown in page.counts:
            listed = sum(1 for item in page.items if item == name)
            assert shown == str(listed), f"{state} {url} says {shown} {name} beside {listed}"
            seen += 1
        undeclared = page.undeclared_numbers()
        assert not undeclared, f"{state} {url} shows a number that counts nothing: {undeclared}"
    # A stranded state is one page with one count on it; every other state is
    # every screen of the walk plus the pages the POST-only routes render, and
    # a walk counting fewer than six things across them has stopped deriving
    # numbers it used to derive.
    floor = 1 if state in (_STALE, _STALE_DESCRIPTION) else 6
    assert seen >= floor, f"{state} counts {seen} things"


def test_the_count_check_catches_a_number_that_does_not_match_its_list() -> None:
    page = read(
        "<main><p><span data-count='file'>4</span> files</p>"
        "<ul><li data-item='file'>a</li><li data-item='file'>b</li>"
        "<li data-item='file'>c</li><li data-item='file'>d</li>"
        "<li data-item='file'>e</li></ul></main>"
    )
    (name, shown) = page.counts[0]
    assert (name, shown) == ("file", "4")
    assert sum(1 for item in page.items if item == name) == 5


def test_the_script_check_sees_an_element_the_page_text_cannot() -> None:
    """The assertion this replaced -- `"<script" not in page.text` -- could
    never fail for any input. `text` is built in `handle_data`, which receives
    character data only, and `_SKIP` drops a script's contents before they
    reach it, so the literal cannot occur there. The same page is read both
    ways here: the text says nothing at all about the script, and the element
    list says what is on the page.
    """
    page = read("<main><h1>Written</h1><script>gate()</script><p>ok</p></main>")
    assert "<script" not in page.text
    assert page.text == "Writtenok"
    assert page.scripts == ("",)
    assert read('<main><script src="/app.js"></script></main>').scripts == ("/app.js",)
    assert read("<main><p>ok</p></main>").scripts == ()


def test_not_null_is_declared_on_no_screen(walk: Walk, walk_sql: Path) -> None:
    """Section 11.4(c): the rebuild emitted `tests: [unique, not_null]` for a
    user who agreed to a uniqueness check and was never asked about nulls.
    Nothing the tool decides is shown as something the user chose, so the
    name of a test nobody was offered appears nowhere -- including in a file
    preview, where it would be about to be written.
    """
    app, client, session = walk(walk_sql)
    key = next(d.key for d in session.view().questions if ".append." in d.key)
    assert (
        client.post(
            "/answer", data={"key": key, "kind": "merge_checked", "typed": "event_id"}
        ).status_code
        == 302
    )
    assert session.view().change.tests, "the checked answer should have declared a test"

    for url, page in _pages(app, client, session).items():
        assert "not_null" not in page.text, f"{url} declares a test nobody chose"


@pytest.mark.parametrize("state", _STATES)
def test_no_screen_gates_the_write_action(
    walk: Walk, sql_script, walk_sql: Path, project_dir: Path, out_dir: Path, state: str
) -> None:
    """Three of three personas named the second acknowledgment checkbox
    theatre and said ticking it would be lying to the tool: it demanded a
    certification the page gave them no way to obtain. The write action is
    reachable with nothing supplied.

    Every control, not only checkboxes. "I have read this" as a required text
    box or a required dropdown is the same gate demanding the same
    certification, built out of a different tag, and a guard that named the
    tag rather than the demand would wave both through.

    The column picker's checkboxes are a different thing -- they carry an
    answer, not an acknowledgment -- so the claim is about the form that
    writes and about `required`, not about controls in general.
    """
    app, client, session, out = _walk_in(walk, sql_script, walk_sql, state, project_dir, out_dir)
    pages = _guarded_pages(app, client, session, state)

    if state not in (_STALE, _STALE_DESCRIPTION):
        done = pages["/done"]
        assert [form for form in done.forms if form.get("action") == "/write"]
        writing = [control for control in done.controls if control["form"] == "/write"]
        assert writing, "the write form offers nothing to press"
        demanded = [control for control in writing if "required" in control]
        assert not demanded, f"the write action demands {demanded}"
        assert not [c for c in writing if c.get("type") == "checkbox"]

    for url, page in pages.items():
        gated = [c for c in page.controls if c.get("type") == "checkbox" and "required" in c]
        assert not gated, f"{state} {url} demands a tick before it will go on"
        assert page.scripts == (), f"{state} {url} carries script that could gate it"


@pytest.mark.parametrize("state", [_PRISTINE, _ANSWERED, _DOWNGRADED, _DESCRIBED])
def test_every_decision_reaches_exactly_one_screen(
    walk: Walk, sql_script, walk_sql: Path, project_dir: Path, out_dir: Path, state: str
) -> None:
    """Nothing is silent. Every Decision this conversion recorded is on a
    screen, and on one screen: a Decision rendered nowhere is a choice made
    without telling anyone, and one rendered twice is the repetition a
    reviewer named as the thing most likely to train a reader to skip it.

    In the answered states too, and that is where it does real work: a
    Decision an answer brought into existence belongs beside the question
    that answer was given to, and the caveats screen has to stop carrying it
    at the same moment the question screen starts. Checked only before the
    first answer, the partition would be checked in the one state where
    nothing has moved.
    """
    app, client, session, out = _walk_in(walk, sql_script, walk_sql, state, project_dir, out_dir)
    pages = _pages(app, client, session)
    decisions = session.view().change.decisions
    assert len(decisions) > 5, "this conversion should record more than a handful"

    for decision in decisions:
        action = normalised(decision.action)
        on = [url for url, page in pages.items() if action in _engine(page)]
        assert len(on) == 1, f"{state}: {decision.key} is rendered on {on or 'no screen'}"


def test_the_cutover_is_disclosed_before_the_first_question(walk: Walk, walk_sql: Path) -> None:
    """Two of five asked for the rename's consequence here rather than at the
    end, and it corrected the most dangerous mapping a persona brought with
    them. It is Tier 1 and asks nothing, so it renders outside any
    question branch -- `Decision.plain_reason` is what carries it.
    """
    app, client, session = walk(walk_sql)
    start = read(client.get("/").get_data(as_text=True))
    renamed = [d for d in session.view().change.decisions if d.key.startswith("assemble.rename.")]
    assert len(renamed) == 3

    rendered = _engine(start)
    for decision in renamed:
        assert normalised(decision.action) in rendered

    # The consequence itself, in the plain register, once -- not the same
    # paragraph three times with two table names changed.
    plain = [d for d in renamed if normalised(d.plain_reason) in rendered]
    assert len(plain) == 1, f"the cutover consequence renders {len(plain)} times"


def test_the_question_screen_carries_both_registers(walk: Walk, walk_sql: Path) -> None:
    """Both wordings, both the engine's, told apart by the same "In plain
    words" opening the report already uses -- not by a heading naming the
    audience, which would sort the readers instead of the sentences.
    """
    app, client, session = walk(walk_sql)
    decision = session.view().questions[0]
    page = read(client.get("/questions/0").get_data(as_text=True))

    rendered = _engine(page)
    assert normalised(decision.question) in rendered
    assert normalised(decision.plain_question) in rendered
    assert normalised(decision.reason) in rendered
    for option in decision.options:
        assert normalised(option.label) in rendered
        assert normalised(option.effect) in rendered
        assert normalised(option.plain) in rendered
    assert "In plain words" in page.text


def test_the_picker_still_offers_columns_after_the_question_has_been_answered(
    walk: Walk, walk_sql: Path
) -> None:
    """The dead end Task 5 measured. Once an answer is applied, the options
    the screen is rendering name their key and carry no `columns_prompt` at
    all, while the answer still goes to the pristine question, which requires
    columns. A picker built from what is on screen would have no way to send
    a second key and the only answer left would be "append".
    """
    app, client, session = walk(walk_sql)
    key = next(d.key for d in session.view().questions if ".append." in d.key)
    index = [d.key for d in session.view().questions].index(key)
    client.post("/answer", data={"key": key, "kind": "merge", "typed": "event_id"})

    answered = next(d for d in session.view().questions if d.key == key)
    assert [option.columns_prompt for option in answered.options] == ["", "", ""]

    page = read(client.get(f"/questions/{index}").get_data(as_text=True))
    assert session.view().prompts[key]["merge"] in page.text
    assert [box for box in page.inputs if box.get("name") == "typed"]

    # The rows, not only the legend and the free-text field. Reading the
    # candidate rows off the rendered option's `columns_prompt` instead of the
    # session's prompts empties the picker after the first answer while
    # leaving both of those in place -- half of the dead end, and invisible to
    # a check that asked only whether the picker was there.
    assert answered.subject is not None
    candidates = answered.subject.candidates
    assert candidates == ("event_id", "occurred_at"), "the model still projects both"
    pickers = [kind for kind, prompt in session.view().prompts[key].items() if prompt]
    ticked = [box.get("value") for box in page.inputs if box.get("name") == "columns"]
    assert ticked == list(candidates) * len(pickers)


def test_a_model_whose_columns_are_not_knowable_is_offered_free_text(
    walk: Walk, sql_script
) -> None:
    """`Subject.candidates` empty means "none known", never "none exist". A
    star projection is the case: the picker has nothing to list and the free
    text field is the whole of it.
    """
    app, client, session = walk(sql_script(STAR_PROJECTION))
    (question,) = session.view().questions
    assert question.subject is not None and question.subject.candidates == ()

    page = read(client.get("/questions/0").get_data(as_text=True))
    assert [item for item in page.items if item == "column"] == []
    assert [box for box in page.inputs if box.get("name") == "columns"] == []
    typed = [box for box in page.inputs if box.get("name") == "typed"]
    assert len(typed) == 2, "each answer that needs columns offers its own free text field"
    assert session.view().prompts[question.key]["merge"] in page.text


def test_a_repeated_candidate_name_is_listed_twice_and_offered_once(walk: Walk, sql_script) -> None:
    """`SELECT o.amount, p.amount` projects two columns of one name, and the
    engine deliberately does not deduplicate them. Both are listed, because
    the ambiguity is the user's to see; one carries the tick, because both
    ticks would send the same string and a choice between two identical
    answers is not a choice.
    """
    app, client, session = walk(sql_script(DUPLICATE_CANDIDATES))
    (question,) = session.view().questions
    assert question.subject is not None and question.subject.candidates == ("amount", "amount")

    pickers = [kind for kind, prompt in session.view().prompts[question.key].items() if prompt]
    assert len(pickers) == 2, "both merge answers ask for the key"

    page = read(client.get("/questions/0").get_data(as_text=True))
    listed = [item for item in page.items if item == "column"]
    ticks = [box for box in page.inputs if box.get("name") == "columns"]

    assert len(listed) == 2 * len(pickers), "each picker lists both projections"
    assert [box.get("value") for box in ticks] == ["amount"] * len(pickers)


def test_the_files_screen_shows_every_file_that_will_be_written_with_its_contents(
    walk: Walk, walk_sql: Path
) -> None:
    """Tom's one change: per-file contents, not a name and a promise. The
    file list is what `emit` writes, so the count beside it is the length of
    that list and the contents are the bytes.
    """
    app, client, session = walk(walk_sql)
    page = read(client.get("/files").get_data(as_text=True))
    rendered = _engine(page)

    written = written_files(session)
    assert len(written) >= 3, "this conversion writes models, a sources file and a report"
    for path, contents in written.items():
        assert normalised(path) in rendered, f"{path} is not on the files screen"
        assert normalised(contents) in rendered, f"{path} is named but its contents are not shown"

    listed = sum(1 for item in page.items if item == "file")
    assert listed == len(written)
    assert ("file", str(len(written))) in page.counts


def test_the_last_screen_names_the_commands_the_glossary_defines(
    walk: Walk, walk_sql: Path
) -> None:
    """Five of five in round two asked for the set of commands related to one
    another; three of four could not tell whether a check runs as part of a
    run. The glossary is where that was written, so the screen names the four
    it defines and adds nothing of its own -- no `--select`, and no trailing
    `+`, which was a hard stop for four of five and which nothing in the
    engine can fill in.
    """
    app, client, session = walk(walk_sql)
    page = read(client.get("/done").get_data(as_text=True))

    named = [item for item in page.items if item == "command"]
    assert len(named) == 4
    rendered = _engine(page)
    for command in ("dbt compile", "dbt run", "dbt test", "dbt build"):
        assert command in rendered, f"{command} is not named"
        (term,) = terms_in(command)
        assert normalised(term.plain) in rendered
    assert "--select" not in page.text
    assert "stg_events+" not in page.text


@pytest.mark.parametrize("state", _STATES)
def test_each_screen_defines_the_dbt_words_it_uses_and_no_others(
    walk: Walk, sql_script, walk_sql: Path, project_dir: Path, out_dir: Path, state: str
) -> None:
    """`terms_in`, not a dumped glossary: four of five personas never reached
    the round-one block that defined everything. Each screen's block is
    exactly the words on that screen.

    In every state, for the reason `test_no_screen_authors_a_sentence` gives:
    a check evaluated only on a pristine, GET-served walk is a check evaluated
    only in the states where it cannot fail. The refusal page is reached by
    the likeliest misstep in the walk, and had no glossary at all until this
    state was added -- `_refused` passed `terms=()` and nothing here noticed.
    """
    app, client, session, out = _walk_in(walk, sql_script, walk_sql, state, project_dir, out_dir)

    for url, page in _guarded_pages(app, client, session, state).items():
        # Read without the block itself. A glossary listing all fourteen
        # terms puts all fourteen words on the page, so a check that read the
        # whole page would find exactly what the block defined however much
        # it defined -- true of any block at all, which is no check.
        used = terms_in(page.outside_asides())
        defined = [item for item in page.items if item == "term"]
        assert len(defined) == len(used), (
            f"{state} {url} defines {len(defined)} words and uses {[t.name for t in used]}"
        )
        rendered = _engine(page)
        for term in used:
            assert term.name in rendered, f"{state} {url} uses {term.name} and does not define it"
            assert normalised(term.plain) in rendered


def test_the_double_bracket_is_defined_on_the_screen_that_first_shows_it(
    walk: Walk, walk_sql: Path
) -> None:
    app, client, _ = walk(walk_sql)
    brackets = read(client.get("/files").get_data(as_text=True))
    assert "{{" in brackets.text
    assert "{{ }}" in _engine(brackets), "the brackets are shown and never explained"


def test_the_start_screens_glossary_does_not_depend_on_the_sql_files_directory_name(
    tmp_path: Path, project_dir: Path, out_dir: Path
) -> None:
    """The path to the reader's own SQL file is an identifier the start
    screen displays, like a Decision key -- not prose the engine spoke. A
    directory the reader happened to name `warehouse` must not put the word
    `warehouse` on the page as though the conversion's own output used it: a
    reader who never sees the word `warehouse` anywhere the engine wrote
    still meets its definition, on the first screen, for no reason connected
    to their conversion.
    """
    from dbtw.web.app import create_app

    plain = tmp_path / "sql" / "in.sql"
    plain.parent.mkdir(parents=True)
    plain.write_text(ONE_APPEND, encoding="utf-8")

    salted = tmp_path / "warehouse" / "in.sql"
    salted.parent.mkdir(parents=True)
    salted.write_text(ONE_APPEND, encoding="utf-8")

    def start_page(sql: Path) -> Page:
        source = Source(
            project=project_dir, out=out_dir, session=Session(project=project_dir, sql=sql)
        )
        app = create_app(source)
        app.testing = True
        return read(app.test_client().get("/").get_data(as_text=True))

    plain_page = start_page(plain)
    salted_page = start_page(salted)

    plain_defined = {item for item in plain_page.items if item == "term"}
    salted_defined = {item for item in salted_page.items if item == "term"}
    assert len(plain_defined) == len(salted_defined), (
        f"plain defines {sorted(t.name for t in terms_in(plain_page.outside_asides()))}, "
        f"salted defines {sorted(t.name for t in terms_in(salted_page.outside_asides()))}"
    )
    assert "warehouse" not in {t.name for t in terms_in(salted_page.outside_asides())}


# Not _DESCRIBED: this test looks for a repeated `Decision.reason`, and the
# describe screen renders no Decision at all, so the extra state would add a
# parameter with nothing in it that could fail -- coverage in the list and
# none in the run.
@pytest.mark.parametrize("state", [_PRISTINE, _ANSWERED, _DOWNGRADED])
def test_no_screen_repeats_one_engine_string(
    walk: Walk, sql_script, walk_sql: Path, project_dir: Path, out_dir: Path, state: str
) -> None:
    """The repetition a reviewer named as the thing most likely to train a
    reader to skip a block. One string, rendered twice on one screen, is
    either a mistake or noise; either way it is not the page saying something
    twice on purpose.
    """
    app, client, session, out = _walk_in(walk, sql_script, walk_sql, state, project_dir, out_dir)
    consequences = {
        normalised(text)
        for decision in session.view().change.decisions
        for text in (decision.reason, decision.plain_reason)
        if len(words(text)) >= _SENTENCE
    }
    for url, page in _pages(app, client, session).items():
        shown = [normalised(run) for run in page.engine]
        repeated = {text: shown.count(text) for text in consequences if shown.count(text) > 1}
        assert not repeated, f"{state} {url} restates a consequence {list(repeated.values())} times"


def test_the_engine_refuses_an_example_for_every_caveat_so_the_screen_shows_none(
    walk: Walk, walk_sql: Path
) -> None:
    """Spec section 11.2 asks for a worked example on every question *and*
    every caveat. The caveat half is not buildable, and this is the test that
    says so rather than a template filling the hole with prose.

    `worked_example`'s first refusal is `not decision.question or
    decision.subject is None`, and every Decision that asks nothing carries
    both. **Dropping that refusal and handing every caveat a subject closes
    none of the eleven** -- measured, not reasoned about. Three further
    blockers stand behind it, and they are independent of each other:

    * seven of the eleven are `assemble.*` Decisions whose keys carry no
      statement index, so `statement_index(d) is None` and the pairing
      `worked_example` uses to find the model returns nothing. Pairing them
      needs a channel the `Decision` contract does not have;
    * two are `truncate_insert` Decisions over a model that is not
      incremental at all, and `worked_example` has two branches -- merge and
      append -- neither of which describes a full rebuild;
    * two are over a `SELECT *`, whose output columns are not knowable at
      convert time, and those are never closable by anything.

    And the shape is wrong for most of them regardless: `Example` models one
    row before and after, while seven of the eleven are not row-shaped. A
    rename's honest picture is two tables, not two rows.

    So this is not a two-line fix waiting to be scoped. It is a core change
    of real size -- a pairing channel, a third `worked_example` branch, and
    an `Example` that can carry something other than a row -- and until it
    lands the caveats screen shows no example and invents none. This test
    fails the day any part of it lands, which is when the screen should be
    revisited.
    """
    app, client, session = walk(walk_sql)
    view = session.view()
    silent = [d for d in view.change.decisions if not d.question]
    assert len(silent) == 11, "this conversion records eleven decisions it asks nothing about"

    for decision in silent:
        assert decision.subject is None, f"{decision.key} now carries a subject"
        for model in view.change.models:
            assert worked_example(decision, model, view.change.dialect) is None

    page = read(client.get("/caveats").get_data(as_text=True))
    furniture = {PLACEHOLDER_NOTICE, SUPPOSED_ROW_LABEL, AFTER_RUN_LABEL}
    assert not _engine(page) & furniture


def test_every_template_the_walk_renders_ships_inside_the_package(
    walk: Walk, walk_sql: Path
) -> None:
    """A template outside the package directory installs from a checkout and
    is missing from a wheel, and the failure arrives as a TemplateNotFound on
    a user's first screen. `[tool.hatch.build.targets.wheel]` ships
    `src/dbtw` whole, so the test is that these files are under it.
    """
    app, _client, _session = walk(walk_sql)
    package = Path(str(dbtw.web.__file__)).parent

    rendered = set(app.jinja_env.list_templates())
    assert rendered == {
        "base.html",
        "entry.html",
        "start.html",
        "question.html",
        "describe.html",
        "changed.html",
        "project.html",
        "caveats.html",
        "files.html",
        "done.html",
        "stale.html",
        "stale_descriptions.html",
        "refused.html",
        "missing.html",
        "written.html",
        "write_failed.html",
    }
    for name in rendered:
        assert (package / "templates" / name).is_file(), name
    assert (package / "static" / "app.css").is_file()


def test_every_screen_links_to_every_other(walk: Walk, walk_sql: Path) -> None:
    """The nav is how a reader reaches a question screen; there is no other
    way in. A nav that lists fewer screens than the walk has is a screen
    nobody can open, and the routing table would go on serving it -- so the
    links are checked against the routes rather than against the nav's own
    idea of how many there are.
    """
    app, client, session = walk(walk_sql)
    urls = set(_screen_urls(app, session))
    assert len(urls) == 9

    for url, page in _pages(app, client, session).items():
        assert set(page.links) == urls, f"{url} links to {sorted(set(page.links))}"
        assert sum(1 for item in page.items if item == "screen") == len(urls)


def test_the_start_screen_names_what_is_being_converted(walk: Walk, walk_sql: Path) -> None:
    """Which script, into which project. Both are the user's own, both are
    what the command was given, and a walk that named neither would be six
    screens about an unnamed thing.
    """
    app, client, session = walk(walk_sql)
    page = read(client.get("/").get_data(as_text=True))
    rendered = _engine(page)

    assert session.view().change.project_name in rendered
    assert str(session.sql) in rendered


def test_an_answer_the_engine_did_not_apply_is_explained_beside_the_question(
    walk: Walk, walk_sql: Path
) -> None:
    """Name a key the model does not select and the engine accepts the answer,
    refuses to apply it, and records why. Before this, the screen the reader
    was redirected to showed "append every row -- chosen" and said nothing;
    the explanation was on the caveats screen, two screens away, in dbt's
    words only.

    The redirect after an answer exists so the reader watches their answer
    take effect. When it does not take effect, that is the thing to show, and
    it is shown where the answer was given.
    """
    app, client, session = walk(walk_sql)
    key = _append_key(session)
    index = [d.key for d in session.view().questions].index(key)
    before = {d.key for d in _pristine_decisions(session)}

    assert (
        client.post("/answer", data={"key": key, "kind": "merge", "typed": "evnt_id"}).status_code
        == 302
    )

    view = session.view()
    (events,) = [m for m in view.change.models if m.name == "stg_events"]
    assert (events.incremental_strategy, events.unique_key) == ("append", ())
    assert next(d for d in view.questions if d.key == key).chosen == "append every row"

    # Named by difference, not by spelling: the Decision an answer produces is
    # the one the pristine run does not carry.
    (downgrade,) = [d for d in view.change.decisions if d.key not in before]

    pages = _pages(app, client, session)
    here = f"/questions/{index}"
    rendered = _engine(pages[here])
    assert normalised(downgrade.action) in rendered, "the screen does not say the key was dropped"
    assert normalised(downgrade.reason) in rendered, "the screen does not say why"

    elsewhere = [
        url
        for url, page in pages.items()
        if url != here and normalised(downgrade.action) in _engine(page)
    ]
    assert elsewhere == [], f"it is also on {elsewhere}"


def test_an_answer_the_engine_applied_as_given_says_nothing_extra(
    walk: Walk, walk_sql: Path
) -> None:
    """The other half of the claim above, and what stops it being a section
    that is always there: an answer that did what it said produces no
    Decision, so the screen gains no block.
    """
    app, client, session = walk(walk_sql)
    key = _append_key(session)
    index = [d.key for d in session.view().questions].index(key)
    before = {d.key for d in _pristine_decisions(session)}

    assert (
        client.post("/answer", data={"key": key, "kind": "merge", "typed": "event_id"}).status_code
        == 302
    )

    view = session.view()
    (events,) = [m for m in view.change.models if m.name == "stg_events"]
    assert (events.incremental_strategy, events.unique_key) == ("merge", ("event_id",))
    assert [d.key for d in view.change.decisions if d.key not in before] == []

    page = read(client.get(f"/questions/{index}").get_data(as_text=True))
    assert [item for item in page.items if item == "produced"] == []
    assert "From your answer" not in page.text


def test_a_page_the_walk_does_not_have_is_still_shaped_like_the_walk(
    walk: Walk, walk_sql: Path
) -> None:
    """A mistyped address, and a question index this conversion does not ask.
    `/questions/9` is one keystroke from a link that works, so it is the 404
    a reader reaches without doing anything odd. Werkzeug's stock page -- no
    navigation, no way back, "check your spelling" -- leaves them outside the
    walk with nothing on the page to get them back into it.
    """
    app, client, session = walk(walk_sql)

    for response in (client.get("/nowhere"), client.get("/questions/9")):
        assert response.status_code == 404
        page = read(response.get_data(as_text=True))
        assert set(page.links) == set(_screen_urls(app, session))
        assert page.named_links["back"] == "/"
        assert page.sentences(_SENTENCE) == ()
        assert page.undeclared_numbers() == ()
        assert "check your spelling" not in page.text


def test_the_refusal_page_goes_back_to_the_question_it_refused(walk: Walk, walk_sql: Path) -> None:
    """The likeliest misstep in the walk: press "merge on a unique key"
    without naming a column. The walk is still around the refusal and the way
    on is the question it came from, not the start of it.
    """
    app, client, session = walk(walk_sql)
    key = _append_key(session)
    index = [d.key for d in session.view().questions].index(key)

    refused = client.post("/answer", data={"key": key, "kind": "merge"})
    assert refused.status_code == 400
    page = read(refused.get_data(as_text=True))

    assert normalised(_refusal_message(session)) in _engine(page)
    # The link the page itself offers, not merely a link on it: every page in
    # the walk carries a nav link to every question, so "the refusal links to
    # /questions/1" is true of the start screen too and says nothing.
    assert page.named_links["back"] == f"/questions/{index}"
    assert set(page.links) >= set(_screen_urls(app, session))


def test_a_refusal_about_a_key_this_conversion_does_not_ask_goes_to_the_start(
    walk: Walk, walk_sql: Path
) -> None:
    """There is no question screen for a key this conversion does not ask, so
    the way on is the walk's first screen -- the only honest destination, and
    not a guess at which question was meant.
    """
    _app, client, _session = walk(walk_sql)
    refused = client.post("/answer", data={"key": "tier2.append.nowhere.sql:0", "kind": "append"})
    assert refused.status_code == 400
    assert read(refused.get_data(as_text=True)).named_links["back"] == "/"


def test_the_glossary_is_set_aside_from_the_screen_it_defines_words_for(
    walk: Walk, walk_sql: Path
) -> None:
    """`data-aside` on the glossary block is what stops the check above being
    a tautology, and it is one attribute. Without it the block's own
    definitions are part of the screen the check reads, so a screen defining
    fourteen words is a screen using fourteen words and the check passes for
    any block at all -- including the round-one block that dumped everything,
    which four of five personas never reached.
    """
    app, client, _session = walk(walk_sql)
    page = read(client.get("/files").get_data(as_text=True))

    defined = terms_in(page.outside_asides())
    assert defined, "this screen uses words the glossary defines"
    assert len(page.outside_asides()) < len(page.text)

    inside = {normalised(run.text) for run in page.runs if run.aside}
    for term in defined:
        assert normalised(term.plain) in inside, f"{term.name} is defined outside the aside"


@pytest.mark.parametrize("state", _STATES)
def test_every_position_shown_is_the_position_it_sits_in(
    walk: Walk, sql_script, walk_sql: Path, project_dir: Path, out_dir: Path, state: str
) -> None:
    """Section 11.4(c) for the other kind of number.

    The design numbers its steps and its pillars, and a number that names a
    position has to answer to something the same way a count does: step 03
    that is fourth in the rail is the same lie as "4 new files" over a list
    of five, told about a different number. So every `data-ordinal` is held
    against its own place among the `data-item` elements it names.
    """
    app, client, session, out = _walk_in(walk, sql_script, walk_sql, state, project_dir, out_dir)

    for url, page in _guarded_pages(app, client, session, state).items():
        seen: dict[str, int] = {}
        for name, shown in page.ordinals:
            seen[name] = seen.get(name, 0) + 1
            assert int(shown) == seen[name], (
                f"{state} {url} shows {name} {shown} in position {seen[name]}"
            )
        for name, count in seen.items():
            listed = sum(1 for item in page.items if item == name)
            assert count == listed, f"{state} {url} numbers {count} {name} and lists {listed}"


@pytest.mark.parametrize("state", _STATES)
def test_the_glossary_is_folded_shut(
    walk: Walk, sql_script, walk_sql: Path, project_dir: Path, out_dir: Path, state: str
) -> None:
    """Each word visible, each meaning one click away.

    `terms_in` gives a screen exactly the words it uses, which is the design
    -- but the files screen uses nine of them and the done screen four, so
    rendered open the block put "Write these files", the one control in the
    walk that touches the reader's disk, below nine hundred pixels of
    glossary. A mutation adding `open` back restores that wall, and before
    this test nothing in the suite noticed.
    """
    app, client, session, out = _walk_in(walk, sql_script, walk_sql, state, project_dir, out_dir)

    for url, body in _guarded_bodies(app, client, session, state).items():
        assert "<details open" not in body, f"{state} {url} renders the glossary unfolded"
        page = read(body)
        terms = sum(1 for item in page.items if item == "term")
        assert body.count("<details") == terms, (
            f"{state} {url} defines {terms} words in {body.count('<details')} folds"
        )


def _guarded_bodies(app, client, session: Session, state: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """The same pages `_guarded_pages` reads, as raw HTML.

    For the one claim the parser cannot model: `page.py` records an
    element's text and its markers, never its other attributes, so whether a
    `<details>` carries `open` is invisible to it. Asserted on the body
    rather than by widening the reader, because "is this block folded" is a
    question about one template and not a property every screen has.
    """
    if state in (_STALE, _STALE_DESCRIPTION):
        stranded = client.get("/")
        assert stranded.status_code == 409
        return {"/ (stranded)": stranded.get_data(as_text=True)}
    bodies: dict[str, str] = {}
    for url in _screen_urls(app, session):
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        bodies[url] = response.get_data(as_text=True)
    entry = client.get(_ENTRY)
    assert entry.status_code == 200
    bodies[_ENTRY] = entry.get_data(as_text=True)
    return bodies


def test_a_question_nobody_has_answered_asks_and_an_answered_one_does_not(
    walk: Walk, walk_sql: Path
) -> None:
    """The dot beside a step is derived from the conversion, not from where
    the reader has got to -- the design's own note on that rail is "position
    in the flow never marks a step done".
    """
    app, client, session = walk(walk_sql)
    key = _append_key(session)
    view = session.view()
    index = next(i for i, d in enumerate(view.questions) if d.key == key)

    # Found by url, not by position. A screen added anywhere ahead of the
    # questions moves them along the rail, and an index arithmetic'd from the
    # question number silently starts asserting about a different screen.
    url = f"/questions/{index}"
    (before,) = [s for s in _screens(view, answered=session.answers) if s.url == url]
    assert before.state == "ask"

    assert client.post("/answer", data={"key": key, "kind": "append"}).status_code == 302
    (after,) = [s for s in _screens(session.view(), answered=session.answers) if s.url == url]

    assert after.state == "ok"


def test_a_question_keeps_asking_even_though_the_engine_proposed_an_answer(
    walk: Walk, sql_script
) -> None:
    """Every question in this walk arrives with a `chosen` on it: the engine
    reads what the SQL does and proposes that. Keying the dot on `chosen`
    would mark the whole walk "defaults stand, safe to skip" -- and it would
    be false, because a question exists here precisely because the engine
    decided it should not decide alone.
    """
    app, client, session = walk(sql_script(ONE_MERGE))
    view = session.view()
    (question,) = view.questions
    assert question.chosen, "this question arrives with a proposal on it"

    (screen,) = [s for s in _screens(view, answered=session.answers) if s.url == "/questions/0"]

    assert screen.state == "ask"


def test_a_screen_with_nothing_to_decide_says_its_defaults_stand(
    walk: Walk, walk_sql: Path
) -> None:
    """The hollow dot is for the screens that ask nothing -- the start, the
    caveats, the files, the last one. If every unanswered question were
    hollow too there would be nothing left for it to mean.
    """
    app, client, session = walk(walk_sql)

    screens = {s.url: s.state for s in _screens(session.view(), answered=session.answers)}

    assert screens["/"] == "stands"
    assert screens["/caveats"] == "stands"
    assert screens["/files"] == "stands"
    assert screens["/done"] == "stands"


def test_the_describe_step_asks_until_every_model_has_a_description(
    walk: Walk, walk_sql: Path
) -> None:
    app, client, session = walk(walk_sql)
    names = [model.name for model in session.view().change.models]

    assert _described_state(session.view().change) == "ask"
    for name in names:
        saved = client.post("/describe", data={"model": name, "text": "One row per thing."})
        assert saved.status_code == 302

    assert _described_state(session.view().change) == "ok"
