"""How a reader moves through the walk, and what a screen keeps for them on
the way: the start screen's way on, the anchors a save comes back to, a
refusal that has its own address, and the typed values a refused save keeps.

Every case here was found by a tester pressing the control in a browser.
Each test names what the tester saw.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from tests.unit.web.conftest import PROJECTS, SQL, Walk
from tests.unit.web.engine_strings import engine_strings
from tests.unit.web.helpers import CHAINED_INSERTS, NO_QUESTIONS, ONE_APPEND
from tests.unit.web.page import normalised, read

from dbtw.core.context import NotADbtProjectError
from dbtw.core.emit import worked_example
from dbtw.core.teach import GLOSSARY
from dbtw.web import Session, Source


def _direct(client) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/mode", data={"mode": "direct", "back": "/"}).status_code == 302


def _append_key(session: Session) -> str:
    return next(d.key for d in session.view().questions if ".append." in d.key)


# 1. The start screen's "Next" ---------------------------------------------


def test_next_on_the_start_screen_goes_to_the_first_question(walk: Walk, walk_sql: Path) -> None:
    """Found by pressing it: "Next" pointed at "/", the start screen itself."""
    _app, client, _session = walk(walk_sql)

    page = read(client.get("/").get_data(as_text=True))

    assert page.named_links["next"] == "/questions/0"


def test_next_on_the_start_screen_goes_to_describe_when_nothing_is_asked(
    walk: Walk, sql_script
) -> None:
    _app, client, session = walk(sql_script(NO_QUESTIONS))
    assert session.view().questions == ()

    page = read(client.get("/").get_data(as_text=True))

    assert page.named_links["next"] == "/describe"


def test_next_on_the_start_screen_goes_to_the_one_screen_in_direct_mode(
    walk: Walk, walk_sql: Path
) -> None:
    _app, client, _session = walk(walk_sql)
    _direct(client)

    page = read(client.get("/").get_data(as_text=True))

    assert page.named_links["next"] == "/everything"


# 2. The worked example in direct mode -------------------------------------


def test_the_one_screen_shows_each_worked_example_it_has_and_no_dead_link(
    walk: Walk, walk_sql: Path
) -> None:
    """Found by pressing "See the example": it pointed at /questions/N, which
    sends a direct reader back to /everything, so the example never showed.
    """
    _app, client, session = walk(walk_sql)
    _direct(client)
    view = session.view()
    examples = [
        next(
            (
                example
                for model in view.change.models
                if (example := worked_example(question, model, view.change.dialect))
            ),
            None,
        )
        for question in view.questions
    ]
    assert any(examples), "this conversion should have at least one worked example"

    page = read(client.get("/everything").get_data(as_text=True))

    assert not [link for link in page.links if link.startswith("/questions/")]
    assert sum(1 for item in page.items if item == "example") == sum(1 for e in examples if e)
    shown = {normalised(run) for run in page.engine}
    for example in examples:
        if example is None:
            continue
        for column in example.columns:
            assert column in shown


def test_the_one_screen_shows_no_example_for_a_question_that_has_none(
    walk: Walk, sql_script
) -> None:
    """The star projection's columns are not knowable, so `worked_example`
    refuses one. The screen then offers neither an example nor a link to one.
    """
    from tests.unit.web.helpers import STAR_PROJECTION

    _app, client, session = walk(sql_script(STAR_PROJECTION))
    _direct(client)
    view = session.view()
    assert view.questions
    assert not [q for q in view.questions for m in view.change.models if worked_example(q, m, None)]

    page = read(client.get("/everything").get_data(as_text=True))

    assert "example" not in page.items
    assert not [link for link in page.links if link.startswith("/questions/")]


# 3. A refused save keeps the reader on their screen, with what they typed --


_JINJA = "Daily totals {{ var('x') }} here"


def test_a_refused_save_on_the_one_screen_stays_there_beside_the_model(
    walk: Walk, walk_sql: Path
) -> None:
    """Found by saving a description the engine refuses: the reader landed on
    the guided describe screen, outside the rail, with the old values in the
    boxes.
    """
    _app, client, session = walk(walk_sql)
    _direct(client)
    name = session.view().change.models[0].name

    refused = client.post(
        "/describe",
        data={"model": name, "text": _JINJA, "tags": "finance daily", "materialization": "view"},
    )

    assert refused.status_code == 302
    assert refused.headers["Location"] == f"/everything#model-{name}"
    landed = client.get(refused.headers["Location"])
    assert landed.status_code == 400
    html = landed.get_data(as_text=True)
    row = html.split(f'id="model-{name}"')[1].split('data-item="model"')[0]
    assert 'class="refusal"' in row, "the refusal is not beside the model it refused"
    assert _JINJA.replace("'", "&#39;") in row, "the typed description is gone"
    assert "finance daily" in row, "the typed tags are gone"
    assert 'value="view" selected' in row, "the typed materialization is gone"
    # Shown once. A reload is a fresh look at what is held.
    again = client.get("/everything")
    assert again.status_code == 200
    assert 'class="refusal"' not in again.get_data(as_text=True)


def test_a_refused_save_is_held_to_the_rules_every_screen_is(
    walk: Walk, walk_sql: Path, out_dir: Path
) -> None:
    """The refused row claims the refusal and the typed values. The refusal
    is the engine's, asked of it by making the call; the typed values are
    what the reader sent, which is all a claim on them may say. Nothing else
    on the page may reach sentence length, and the glossary still defines
    exactly the words the page uses.
    """
    from dbtw.core.teach import terms_in

    _app, client, session = walk(walk_sql)
    _direct(client)
    name = session.view().change.models[0].name
    try:
        session.configure(name, _JINJA, ("finance daily",), "view")
    except ValueError as refusal:
        message = str(refusal)
    else:
        raise AssertionError("the jinja description was accepted")
    produced = engine_strings(session, out_dir) | {
        normalised(message),
        normalised(_JINJA),
        "finance daily",
    }

    page = read(
        client.post(
            "/describe",
            data={
                "model": name,
                "text": _JINJA,
                "tags": "finance daily",
                "materialization": "view",
            },
            follow_redirects=True,
        ).get_data(as_text=True)
    )

    unclaimed = [run for run in page.engine if normalised(run) not in produced]
    assert not unclaimed, unclaimed[:2]
    assert not page.sentences(5), page.sentences(5)
    used = terms_in(page.outside_asides())
    assert sum(1 for item in page.items if item == "term") == len(used)


def test_the_one_screen_defines_the_words_its_examples_use(walk: Walk, walk_sql: Path) -> None:
    """The examples are on the page now, so their words are words the page
    uses, and the glossary has to define them.
    """
    from dbtw.core.teach import terms_in

    _app, client, _session = walk(walk_sql)
    _direct(client)

    page = read(client.get("/everything").get_data(as_text=True))

    used = terms_in(page.outside_asides())
    assert sum(1 for item in page.items if item == "term") == len(used)
    shown = {normalised(run) for run in page.engine}
    for term in used:
        assert term.name in shown, term.name


def test_a_refused_save_on_the_describe_screen_stays_there_beside_the_model(
    walk: Walk, walk_sql: Path
) -> None:
    _app, client, session = walk(walk_sql)
    name = session.view().change.models[0].name

    refused = client.post("/describe", data={"model": name, "text": _JINJA})

    assert refused.headers["Location"] == f"/describe#model-{name}"
    landed = client.get("/describe")
    assert landed.status_code == 400
    row = landed.get_data(as_text=True).split(f'id="model-{name}"')[1]
    assert 'class="refusal"' in row.split('data-item="model"')[0]


def test_a_save_refused_on_one_field_records_none_of_the_three(walk: Walk, walk_sql: Path) -> None:
    """The route called describe, tag and materialize in turn, so a refused
    materialization left the description and the tags recorded -- under a
    comment that said nothing was.
    """
    _app, client, session = walk(walk_sql)
    name = session.view().change.models[0].name

    refused = client.post(
        "/describe",
        data={
            "model": name,
            "text": "One row per event.",
            "tags": "finance",
            "materialization": "incremental",
        },
        follow_redirects=True,
    )

    assert refused.status_code == 400
    assert session.descriptions == {}
    assert session.tags == {}
    assert session.materializations == {}


def test_a_save_refused_on_the_description_records_no_tag(walk: Walk, walk_sql: Path) -> None:
    _app, client, session = walk(walk_sql)
    name = session.view().change.models[0].name

    client.post("/describe", data={"model": name, "text": _JINJA, "tags": "finance"})

    assert session.descriptions == {}
    assert session.tags == {}


def test_configure_records_all_three_or_none(walk: Walk, walk_sql: Path) -> None:
    _app, _client, session = walk(walk_sql)
    name = session.view().change.models[0].name

    with pytest.raises(ValueError):
        session.configure(name, "One row.", ("finance",), "incremental")
    assert (session.descriptions, session.tags, session.materializations) == ({}, {}, {})

    session.configure(name, "One row.", ("finance",), "view")
    assert session.descriptions == {name: "One row."}
    assert session.tags == {name: ("finance",)}
    assert session.materializations == {name: "view"}

    session.configure(name, "", (), "")
    assert session.descriptions == {name: ""}
    assert session.tags == {}
    assert session.materializations == {}


# 4. "Also reading" takes one path per line ---------------------------------


def test_also_reading_is_a_box_that_holds_more_than_one_line(project_dir: Path, out_dir: Path):
    """Found by typing two paths: a one-line input joined them with a space."""
    from dbtw.web.app import create_app

    app = create_app(Source(out=out_dir))
    app.testing = True

    page = read(app.test_client().get("/source").get_data(as_text=True))

    (control,) = [c for c in page.controls if c.get("name") == "elsewhere"]
    assert control["tag"] == "textarea"


def test_two_other_projects_on_two_lines_are_read_as_two(project_dir: Path, out_dir: Path) -> None:
    from dbtw.web.app import create_app

    other = project_dir.parent / "other"
    shutil.copytree(PROJECTS / "core_platform", other)
    source = Source(out=out_dir)
    app = create_app(source)
    app.testing = True

    sent = app.test_client().post(
        "/source",
        data={
            "project": str(project_dir),
            "elsewhere": f"{other}\n\n{project_dir}\n",
            "pasted": ONE_APPEND,
            "name": "revenue",
        },
    )

    assert sent.status_code == 302
    assert source.elsewhere == (other, project_dir)


# 5. Each rail label names one screen ---------------------------------------


def _rail_labels(html: str) -> list[str]:
    labels = re.findall(r'<span class="label">(.*?)</span>\s*<span class="dot', html, re.S)
    return [" ".join(re.sub(r"<[^>]+>", " ", label).split()) for label in labels]


def test_two_questions_about_one_model_have_two_labels(tmp_path: Path, out_dir: Path) -> None:
    """Found in the rail: the append question and the layer question on one
    model were both labelled with the model's name.
    """
    from dbtw.web.app import create_app

    project = tmp_path / "three_layers"
    shutil.copytree(PROJECTS / "three_layers", project)
    sql = tmp_path / "sql" / "in.sql"
    sql.parent.mkdir()
    sql.write_text(CHAINED_INSERTS, encoding="utf-8")
    session = Session(project=project, sql=sql)
    tables = [q.subject.table for q in session.view().questions if q.subject]
    assert len(tables) != len(set(tables)), "the fixture should ask twice about one model"
    app = create_app(Source(project=project, out=out_dir, session=session))
    app.testing = True

    labels = _rail_labels(app.test_client().get("/").get_data(as_text=True))

    assert len(labels) == len(set(labels)), labels
    for table in tables:
        assert sum(1 for label in labels if label.startswith(table)) == tables.count(table)


# Every script the tests converse over, beside the fixture corpus. Together
# they make the engine ask each kind of question it can ask: append, merge,
# layer, variable, and -- with another project named -- cross_ref.
_CROSS = (
    "INSERT INTO revenue_events SELECT c.name, o.amount "
    "FROM analytics.dim_customers AS c JOIN analytics.orders AS o ON o.cid = c.customer_id;\n"
    "INSERT INTO revenue_daily SELECT name, amount FROM revenue_events;\n"
)


def _every_question(tmp_path: Path) -> list:  # type: ignore[type-arg]
    from tests.unit.web import helpers

    scripts = [path.read_text(encoding="utf-8") for path in sorted(SQL.glob("*.sql"))]
    scripts += [value for name, value in vars(helpers).items() if name.isupper()]
    scripts.append(_CROSS)
    questions = []
    for number, text in enumerate(scripts):
        sql = tmp_path / f"script{number}" / "in.sql"
        sql.parent.mkdir()
        sql.write_text(text, encoding="utf-8")
        for project in ("jaffle_shop", "three_layers"):
            # T-SQL too: a DECLARE is a script variable only in that dialect.
            for dialect in (None, "tsql"):
                session = Session(
                    project=PROJECTS / project,
                    sql=sql,
                    dialect=dialect,
                    elsewhere=(PROJECTS / "core_platform",),
                )
                questions.extend(session.view().questions)
    return questions


def test_every_question_the_corpus_asks_has_a_plain_kind_word(tmp_path: Path) -> None:
    """Review finding: the rail showed raw key words such as `cross_ref`, and a
    key of an unexpected shape raised on every screen. Every question the
    engine asks over the whole corpus gets a word from the closed list.
    """
    from dbtw.web.app import _KIND_WORDS, _kind

    questions = _every_question(tmp_path)
    kinds = {question.key.split(".")[1] for question in questions}
    assert {"append", "merge", "layer", "variable", "cross_ref"} <= kinds, kinds

    for question in questions:
        word = _kind(question)
        assert word in _KIND_WORDS.values(), (question.key, word)
    assert _KIND_WORDS["cross_ref"] == "another project"


def test_a_question_key_of_no_known_shape_names_no_kind() -> None:
    """Total: a key the list does not know gives no word, and the screen
    still renders. The corpus test above is where an unknown kind fails.
    """
    from dbtw.core.passes import Decision
    from dbtw.web.app import _kind

    for key in ("odd", "tier2.", "assemble.newkind.x", ""):
        decision = Decision(
            key=key, tier=2, action="a", reason="r", source_file="", line_start=0, line_end=0
        )
        assert _kind(decision) == "", key


# 6. A save comes back to the row it saved ----------------------------------


def test_an_answer_on_the_one_screen_comes_back_to_its_question(walk: Walk, walk_sql: Path) -> None:
    """Found by answering the second question: the screen reloaded at the
    top, and the reader scrolled back down to find their answer.
    """
    _app, client, session = walk(walk_sql)
    _direct(client)
    key = _append_key(session)
    index = [d.key for d in session.view().questions].index(key)

    answered = client.post("/answer", data={"key": key, "kind": "append"})

    assert answered.headers["Location"] == f"/everything#question-{index}"
    assert f'id="question-{index}"' in client.get("/everything").get_data(as_text=True)


def test_a_save_on_the_one_screen_comes_back_to_its_model(walk: Walk, walk_sql: Path) -> None:
    _app, client, session = walk(walk_sql)
    _direct(client)
    name = session.view().change.models[-1].name

    saved = client.post("/describe", data={"model": name, "text": "One row per day."})

    assert saved.headers["Location"] == f"/everything#model-{name}"
    assert f'id="model-{name}"' in client.get("/everything").get_data(as_text=True)


def test_a_save_on_the_describe_screen_comes_back_to_its_model(walk: Walk, walk_sql: Path) -> None:
    _app, client, session = walk(walk_sql)
    name = session.view().change.models[-1].name

    saved = client.post("/describe", data={"model": name, "text": "One row per day."})

    assert saved.headers["Location"] == f"/describe#model-{name}"
    assert f'id="model-{name}"' in client.get("/describe").get_data(as_text=True)


def test_the_old_address_of_a_question_lands_on_it_in_direct_mode(
    walk: Walk, walk_sql: Path
) -> None:
    _app, client, _session = walk(walk_sql)
    _direct(client)

    sent = client.get("/questions/1")

    assert sent.headers["Location"] == "/everything#question-1"


# 7. An entry refusal names the box it is about -----------------------------


def _refusal_label(html: str) -> str:
    block = html.split('<p class="refusal">')[1].split("</p>")[0]
    label = re.sub(r"<span data-engine>.*?</span>", "", block, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", "", label).split())


def test_a_blank_project_is_refused_with_its_own_message(project_dir: Path, out_dir: Path) -> None:
    """Found by typing spaces: "no dbt_project.yml at " with nothing after it."""
    from dbtw.web.app import create_app

    source = Source(out=out_dir)
    app = create_app(source)
    app.testing = True
    try:
        source.start("   ", {"q.sql": ONE_APPEND})
    except NotADbtProjectError as refusal:
        expected = str(refusal)
    else:
        raise AssertionError("a blank project was accepted")

    refused = app.test_client().post(
        "/source", data={"project": "   ", "pasted": ONE_APPEND, "name": "q"}
    )

    assert refused.status_code == 400
    page = read(refused.get_data(as_text=True))
    assert normalised(expected) in {normalised(run) for run in page.engine}
    assert "dbt_project.yml at" not in expected
    assert _refusal_label(refused.get_data(as_text=True)) == "Converting against:"


def test_a_mistyped_other_project_is_refused_as_that_box(project_dir: Path, out_dir: Path) -> None:
    """Found by mistyping the "Also reading" path: the message was the one a
    bad project path gives, so the reader looked in the wrong box.
    """
    from dbtw.web.app import create_app

    app = create_app(Source(out=out_dir))
    app.testing = True

    refused = app.test_client().post(
        "/source",
        data={
            "project": str(project_dir),
            "elsewhere": str(project_dir.parent / "no_such_project"),
            "pasted": ONE_APPEND,
            "name": "q",
        },
    )

    assert refused.status_code == 400
    assert _refusal_label(refused.get_data(as_text=True)) == "Other dbt projects:"


def test_a_mistyped_project_is_refused_as_that_box(project_dir: Path, out_dir: Path) -> None:
    from dbtw.web.app import create_app

    app = create_app(Source(out=out_dir))
    app.testing = True

    refused = app.test_client().post(
        "/source",
        data={"project": str(project_dir.parent / "nope"), "pasted": ONE_APPEND, "name": "q"},
    )

    assert refused.status_code == 400
    assert _refusal_label(refused.get_data(as_text=True)) == "Converting against:"


def test_an_empty_box_refusal_names_no_box(project_dir: Path, out_dir: Path) -> None:
    """Nothing pasted is not about either path box, so no path label is put
    on it.
    """
    from dbtw.web.app import create_app

    app = create_app(Source(out=out_dir))
    app.testing = True

    refused = app.test_client().post("/source", data={"project": str(project_dir), "pasted": ""})

    assert refused.status_code == 400
    assert _refusal_label(refused.get_data(as_text=True)) == ""


# 9. The glossary renders code as code, and no identifier feeds it ----------


def test_the_glossary_renders_a_code_span_as_code(walk: Walk, walk_sql: Path) -> None:
    """Found on screen: "`staging`" with its backticks, in the definition."""
    _app, client, _session = walk(walk_sql)
    staging = next(term for term in GLOSSARY if term.name == "staging")
    assert "`" in staging.plain, "the fixture term should carry a code span"

    html = client.get("/").get_data(as_text=True)

    glossary = html.split('<section class="glossary"')[1].split("</section>")[0]
    assert "staging" in glossary, "the start screen should define staging"
    assert "`" not in glossary
    assert "<code>staging</code>" in glossary


def test_a_project_name_does_not_feed_the_glossary(tmp_path: Path, out_dir: Path) -> None:
    """Found on /project: a project called warehouse_core put "warehouse" in
    "Words used here", and the word was nowhere on the page.
    """
    from dbtw.core.teach import terms_in
    from dbtw.web.app import create_app

    project = tmp_path / "warehouse_core"
    shutil.copytree(PROJECTS / "jaffle_shop", project)
    config = project / "dbt_project.yml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("jaffle_shop", "warehouse_core"),
        encoding="utf-8",
    )
    sql = tmp_path / "sql" / "in.sql"
    sql.parent.mkdir()
    sql.write_text(ONE_APPEND, encoding="utf-8")
    session = Session(project=project, sql=sql)
    app = create_app(Source(project=project, out=out_dir, session=session))
    app.testing = True

    page = read(app.test_client().get("/project").get_data(as_text=True))

    used = terms_in(page.outside_asides())
    assert "warehouse" not in {term.name for term in used}
    assert sum(1 for item in page.items if item == "term") == len(used)


# 10. A refused answer has an address -----------------------------------------


def test_a_refused_answer_lands_on_a_page_with_its_own_address(walk: Walk, walk_sql: Path) -> None:
    """Found by reloading a refusal: the browser asked to send the form again."""
    _app, client, session = walk(walk_sql)
    key = _append_key(session)
    index = [d.key for d in session.view().questions].index(key)

    refused = client.post("/answer", data={"key": key, "kind": "merge"})

    assert refused.status_code == 302
    assert refused.headers["Location"] == "/refused"
    landed = client.get("/refused")
    assert landed.status_code == 400
    page = read(landed.get_data(as_text=True))
    assert any("got no columns" in run for run in page.engine)
    # The path: the link also carries the refused answer, as a query.
    assert urlsplit(page.named_links["back"]).path == f"/questions/{index}"
    # Shown once: a reload goes to the question, and sends nothing.
    reloaded = client.get("/refused")
    assert reloaded.status_code == 302
    assert urlsplit(reloaded.headers["Location"]).path == f"/questions/{index}"
    assert session.answers == {}


def test_the_refusal_page_with_nothing_refused_goes_to_the_start(
    walk: Walk, walk_sql: Path
) -> None:
    _app, client, _session = walk(walk_sql)

    sent = client.get("/refused")

    assert sent.status_code == 302
    assert sent.headers["Location"] == "/"


def test_a_refusal_on_the_one_screen_goes_back_to_its_question(walk: Walk, walk_sql: Path) -> None:
    _app, client, session = walk(walk_sql)
    _direct(client)
    key = _append_key(session)
    index = [d.key for d in session.view().questions].index(key)

    page = read(
        client.post("/answer", data={"key": key, "kind": "merge"}, follow_redirects=True).get_data(
            as_text=True
        )
    )

    back = urlsplit(page.named_links["back"])
    assert (back.path, back.fragment) == ("/everything", f"question-{index}")


# A held refusal lives for one request -----------------------------------------


def test_a_refused_answer_not_followed_does_not_come_back_later(walk: Walk, walk_sql: Path) -> None:
    """Review finding: a refusal whose redirect was never followed stayed
    held, and came back with a 400 on a later, unrelated visit.
    """
    _app, client, session = walk(walk_sql)
    client.post("/answer", data={"key": _append_key(session), "kind": "merge"})

    assert client.get("/caveats").status_code == 200
    later = client.get("/refused")

    assert later.status_code == 302


def test_a_refused_save_not_followed_does_not_come_back_later(walk: Walk, walk_sql: Path) -> None:
    _app, client, session = walk(walk_sql)
    name = session.view().change.models[0].name
    client.post("/describe", data={"model": name, "text": _JINJA})

    assert client.get("/files").status_code == 200
    later = client.get("/describe")

    assert later.status_code == 200
    assert 'class="refusal"' not in later.get_data(as_text=True)


def test_a_refused_save_does_not_survive_a_successful_one(walk: Walk, walk_sql: Path) -> None:
    _app, client, session = walk(walk_sql)
    name = session.view().change.models[0].name
    client.post("/describe", data={"model": name, "text": _JINJA})

    saved = client.post("/describe", data={"model": name, "text": "One row per event."})
    landed = client.get(saved.headers["Location"])

    assert landed.status_code == 200
    assert 'class="refusal"' not in landed.get_data(as_text=True)


def test_a_refused_save_does_not_survive_a_new_conversation(
    walk: Walk, walk_sql: Path, project_dir: Path
) -> None:
    _app, client, session = walk(walk_sql)
    name = session.view().change.models[0].name
    client.post("/describe", data={"model": name, "text": _JINJA})

    started = client.post(
        "/source", data={"project": str(project_dir), "pasted": ONE_APPEND, "name": name}
    )
    assert started.status_code == 302
    landed = client.get("/describe")

    assert landed.status_code == 200
    assert 'class="refusal"' not in landed.get_data(as_text=True)


# 11. A long output path wraps at its separators ------------------------------


@pytest.mark.parametrize("url", ["/done", "/written"])
def test_the_output_path_wraps_at_its_separators(walk: Walk, walk_sql: Path, url: str) -> None:
    app, client, _session = walk(walk_sql)
    out = str(app.config["dbtw_source"].out)
    if url == "/written":
        assert client.post("/write").status_code == 302

    html = client.get(url).get_data(as_text=True)

    destination = html.split('<p class="destination">')[1].split("</p>")[0]
    # Every "/" closes a folder's span and offers a break after it.
    assert destination.count("/</span><wbr>") == out.count("/")
    page = read(html)
    assert out in {normalised(run) for run in page.engine}


def test_a_reload_of_an_old_refusal_in_a_new_conversation_goes_to_the_start(
    walk: Walk, walk_sql: Path, project_dir: Path
) -> None:
    """The old question may not exist in the new conversation, so a reload
    of the refusal page goes to the start rather than to the old question.
    """
    _app, client, session = walk(walk_sql)
    client.post("/answer", data={"key": _append_key(session), "kind": "merge"})
    assert client.get("/refused").status_code == 400
    started = client.post(
        "/source", data={"project": str(project_dir), "pasted": ONE_APPEND, "name": "q"}
    )
    assert started.status_code == 302

    reloaded = client.get("/refused")

    assert reloaded.headers["Location"] == "/"
