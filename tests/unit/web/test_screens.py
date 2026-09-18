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
from tests.unit.web.helpers import DUPLICATE_CANDIDATES, STAR_PROJECTION
from tests.unit.web.page import Page, normalised, read, words

import dbtw.web
from dbtw.core.emit import (
    AFTER_RUN_LABEL,
    PLACEHOLDER_NOTICE,
    SUPPOSED_ROW_LABEL,
    worked_example,
)
from dbtw.core.teach import terms_in
from dbtw.web import Session

# What counts as a sentence in an authored run. Every label the templates
# write is three words or fewer ("In plain words", "Write these files"), so
# five is comfortably above the longest of them and comfortably below any
# sentence that explains something. It is deliberately low: this test exists
# to fail the moment a template starts explaining.
_SENTENCE = 5


def _screen_urls(app, session: Session) -> tuple[str, ...]:
    """Every screen of the walk, derived from the app's own routing table.

    Not a list written here. A per-screen test given a hand-kept list covers
    whatever the list happens to name, and the walk grew a screen the day
    somebody added a route. Each GET rule contributes one URL, except the
    question rule, which contributes one per question this conversion asks.
    A rule taking any other argument stops the test rather than being skipped
    -- a screen this cannot address is a screen nothing here checks.
    """
    asked = len(session.view().questions)
    urls: list[str] = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda rule: rule.rule):
        methods = rule.methods or set()
        if rule.endpoint == "static" or "GET" not in methods:
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


def _pages(app, client, session: Session) -> dict[str, Page]:
    pages: dict[str, Page] = {}
    for url in _screen_urls(app, session):
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        pages[url] = read(response.get_data(as_text=True))
    return pages


def test_the_walk_is_the_six_screens_its_own_routes_define(walk: Walk, walk_sql: Path) -> None:
    """The conversion the ten walkthroughs were run against asks two
    questions, so the walk is six screens: start, two questions, caveats,
    files, done.
    """
    app, client, session = walk(walk_sql)

    urls = _screen_urls(app, session)

    assert urls == ("/", "/caveats", "/done", "/files", "/questions/0", "/questions/1")
    assert len(urls) == 6
    for url in urls:
        assert client.get(url).status_code == 200, url


def test_no_screen_authors_a_sentence(walk: Walk, walk_sql: Path) -> None:
    """Spec section 6.1, mechanically.

    Two assertions, and they are one claim seen from both ends. A template
    that wrote a sentence of its own fails the second; a template that wrote
    one and marked it as the engine's fails the first, because the mark is
    checked by exact match against what `dbtw.core` actually produced.
    """
    app, client, session = walk(walk_sql)
    produced = engine_strings(session)

    for url, page in _pages(app, client, session).items():
        unclaimed = [run for run in page.engine if normalised(run) not in produced]
        assert not unclaimed, f"{url} marks text the engine never produced: {unclaimed[:2]}"

        authored = page.sentences(_SENTENCE)
        assert not authored, f"{url} authors prose: {authored}"


def test_the_prose_check_sees_a_sentence_a_template_would_have_written(
    walk: Walk, walk_sql: Path
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
    produced = engine_strings(session)
    assert [run for run in salted.engine if normalised(run) not in produced]


def test_the_prose_check_has_something_left_to_look_at(walk: Walk, walk_sql: Path) -> None:
    """Every screen leaves authored text behind after the engine's is set
    aside -- headings, nav, buttons. A screen whose authored side came back
    empty would pass the check above having inspected nothing.
    """
    app, client, session = walk(walk_sql)
    for url, page in _pages(app, client, session).items():
        assert page.authored, f"{url} leaves nothing authored to inspect"
        assert page.engine, f"{url} renders nothing from the engine"


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


def test_no_screen_renders_a_heading_with_an_empty_body(walk: Walk, walk_sql: Path) -> None:
    app, client, session = walk(walk_sql)
    for url, page in _pages(app, client, session).items():
        assert page.empty_headings() == (), f"{url} opens a section and says nothing in it"


def test_the_empty_heading_check_catches_one(walk: Walk, walk_sql: Path) -> None:
    app, client, session = walk(walk_sql)
    body = client.get("/files").get_data(as_text=True)
    salted = body.replace("</main>", "<h2>Orphan</h2></main>")
    assert read(salted).empty_headings() == ("Orphan",)


def test_every_count_is_the_length_of_what_is_listed_beside_it(walk: Walk, walk_sql: Path) -> None:
    """Section 11.4(c). The number and the list are checked against each
    other on the page itself, which is the failure that cost the most trust:
    "4 new files" beside a list of five.
    """
    app, client, session = walk(walk_sql)
    seen = 0
    for url, page in _pages(app, client, session).items():
        assert page.counts, f"{url} shows no derived count"
        for name, shown in page.counts:
            listed = sum(1 for item in page.items if item == name)
            assert shown == str(listed), f"{url} says {shown} {name} beside {listed}"
            seen += 1
    assert seen >= 6, "the walk should be counting more than one thing"


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


def test_no_screen_gates_the_write_action_behind_a_checkbox(walk: Walk, walk_sql: Path) -> None:
    """Three of three personas named the second acknowledgment checkbox
    theatre and said ticking it would be lying to the tool: it demanded a
    certification the page gave them no way to obtain. The write action is
    reachable with nothing ticked.

    The column picker's checkboxes are a different thing -- they carry an
    answer, not an acknowledgment -- so the claim is about the screen holding
    the write action and about `required`, not about checkboxes in general.
    """
    app, client, session = walk(walk_sql)
    pages = _pages(app, client, session)

    done = pages["/done"]
    assert [form for form in done.forms if form.get("action") == "/write"]
    assert not [box for box in done.inputs if box.get("type") == "checkbox"]

    for url, page in pages.items():
        gated = [box for box in page.inputs if box.get("type") == "checkbox" and "required" in box]
        assert not gated, f"{url} demands a tick before it will go on"
        assert "<script" not in page.text, f"{url} carries script that could gate it"


def test_every_decision_reaches_exactly_one_screen(walk: Walk, walk_sql: Path) -> None:
    """Nothing is silent. Every Decision this conversion recorded is on a
    screen, and on one screen: a Decision rendered nowhere is a choice made
    without telling anyone, and one rendered twice is the repetition a
    reviewer named as the thing most likely to train a reader to skip it.
    """
    app, client, session = walk(walk_sql)
    pages = _pages(app, client, session)
    decisions = session.view().change.decisions
    assert len(decisions) > 5, "this conversion should record more than a handful"

    for decision in decisions:
        action = normalised(decision.action)
        on = [url for url, page in pages.items() if action in _engine(page)]
        assert len(on) == 1, f"{decision.key} is rendered on {on or 'no screen'}"


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


def test_each_screen_defines_the_dbt_words_it_uses_and_no_others(
    walk: Walk, walk_sql: Path
) -> None:
    """`terms_in`, not a dumped glossary: four of five personas never reached
    the round-one block that defined everything. Each screen's block is
    exactly the words on that screen, which also puts the double-bracket
    definition on the screen that first shows one.
    """
    app, client, session = walk(walk_sql)
    pages = _pages(app, client, session)

    for url, page in pages.items():
        # Read without the block itself. A glossary listing all fourteen
        # terms puts all fourteen words on the page, so a check that read the
        # whole page would find exactly what the block defined however much
        # it defined -- true of any block at all, which is no check.
        used = terms_in(page.outside_asides())
        defined = [item for item in page.items if item == "term"]
        assert len(defined) == len(used), (
            f"{url} defines {len(defined)} words and uses {[t.name for t in used]}"
        )
        rendered = _engine(page)
        for term in used:
            assert term.name in rendered, f"{url} uses {term.name} and does not define it"
            assert normalised(term.plain) in rendered

    brackets = read(client.get("/files").get_data(as_text=True))
    assert "{{" in brackets.text
    assert "{{ }}" in _engine(brackets), "the brackets are shown and never explained"


@pytest.mark.parametrize("url", ["/", "/questions/0", "/caveats", "/files", "/done"])
def test_no_screen_repeats_one_engine_string(walk: Walk, walk_sql: Path, url: str) -> None:
    """The repetition a reviewer named as the thing most likely to train a
    reader to skip a block. One string, rendered twice on one screen, is
    either a mistake or noise; either way it is not the page saying something
    twice on purpose.
    """
    app, client, session = walk(walk_sql)
    page = read(client.get(url).get_data(as_text=True))
    consequences = {
        normalised(text)
        for decision in session.view().change.decisions
        for text in (decision.reason, decision.plain_reason)
        if len(words(text)) >= _SENTENCE
    }
    shown = [normalised(run) for run in page.engine]
    repeated = {text: shown.count(text) for text in consequences if shown.count(text) > 1}
    assert not repeated, f"{url} restates one consequence {list(repeated.values())} times"


def test_the_engine_refuses_an_example_for_every_caveat_so_the_screen_shows_none(
    walk: Walk, walk_sql: Path
) -> None:
    """Spec section 11.2 asks for a worked example on every question *and*
    every caveat. Half of it is unbuildable from here, and this is the test
    that says so rather than a template filling the hole with prose.

    `worked_example`'s first refusal is `not decision.question or
    decision.subject is None`, and every Decision that asks nothing carries
    both -- no caveat this pipeline emits has a subject. So the screen shows
    no example beside a caveat, and invents none.

    Closing it is a core change, not a template one: a caveat Decision has to
    carry its `Subject`, and `worked_example` has to accept a Decision that
    asks nothing. This test fails the day either lands, which is when the
    screen should start rendering them.
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
        "start.html",
        "question.html",
        "caveats.html",
        "files.html",
        "done.html",
        "stale.html",
        "refused.html",
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
    assert len(urls) == 6

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
