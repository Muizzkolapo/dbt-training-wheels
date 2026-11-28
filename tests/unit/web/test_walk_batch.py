"""A batch of defects that browser testers found on the walk.

Each test names what the tester saw. Each one drives the Flask test client,
because each defect was a page a reader landed on.
"""

from __future__ import annotations

import re
import subprocess
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from tests.unit.web.conftest import Walk
from tests.unit.web.helpers import ONE_APPEND, UNPARSEABLE_AND_APPEND
from tests.unit.web.page import normalised, read

from dbtw.web import Session, Source


def _direct(client) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/mode", data={"mode": "direct", "back": "/"}).status_code == 302


def _append_key(session: Session) -> str:
    return next(d.key for d in session.view().questions if ".append." in d.key)


def _rail(html: str) -> list[str]:
    """The rail's step addresses, in rail order."""
    nav = html.split("<nav>")[1].split("</nav>")[0]
    return re.findall(r'<li data-item="screen"[^>]*>\s*<a href="([^"]+)"', nav)


# 3. "Next" on every screen ---------------------------------------------------


@pytest.mark.parametrize("mode", ["guided", "direct"])
def test_every_screen_but_the_last_has_next_to_the_following_screen(
    walk: Walk, walk_sql: Path, mode: str
) -> None:
    """Found by testers: only Start had a "Next" link."""
    _app, client, session = walk(walk_sql)
    if mode == "direct":
        _direct(client)
    # After an answer too: the question screen a reader lands on then is the
    # one that had no way on.
    answer = {"key": _append_key(session), "kind": "merge_checked", "typed": "event_id"}
    assert client.post("/answer", data=answer).status_code == 302

    rail = _rail(client.get("/").get_data(as_text=True))
    assert rail[-1] == "/done"
    for here, following in zip(rail, rail[1:], strict=False):
        page = read(client.get(here).get_data(as_text=True))
        assert page.named_links.get("next") == following, here
    assert "next" not in read(client.get(rail[-1]).get_data(as_text=True)).named_links


# 1. An answer's record in direct mode ---------------------------------------


def test_an_answer_s_record_is_beside_its_question_on_the_one_screen(
    walk: Walk, sql_script, walk_sql: Path
) -> None:
    """Found by testers: the record an answer produced shows only on the
    guided question screen. Direct mode has no question screens, so it
    showed nowhere.
    """
    from tests.unit.web.test_screens import _unknowable

    _app, client, session = walk(sql_script(_unknowable(walk_sql)))
    _direct(client)
    key = _append_key(session)
    assert (
        client.post("/answer", data={"key": key, "kind": "merge", "typed": "evnt_id"}).status_code
        == 302
    )
    before = {
        d.key for d in Session(project=session.project, sql=session.sql).view().change.decisions
    }
    produced = [d for d in session.view().change.decisions if d.key not in before]
    assert produced, "the downgraded answer should put a record on the page"

    html = client.get("/everything").get_data(as_text=True)
    index = [d.key for d in session.view().questions].index(key)
    block = html.split(f'id="question-{index}"')[1].split('<li data-item="ask"')[0]
    shown = normalised(unescape(re.sub(r"<[^>]+>", " ", block)))
    for decision in produced:
        assert normalised(decision.action) in shown, decision.key
    # And nowhere else on the page: said once, beside its question.
    whole = normalised(unescape(re.sub(r"<[^>]+>", " ", html)))
    for decision in produced:
        assert whole.count(normalised(decision.action)) == 1, decision.key


# 2. Unsaved text in direct mode ---------------------------------------------


def test_the_screens_with_model_rows_carry_the_unsaved_text_guard(
    walk: Walk, walk_sql: Path
) -> None:
    """Found by testers: text typed in one row was lost when another row was
    saved. The guard is a script on the two screens with model rows only.
    """
    _app, client, _session = walk(walk_sql)
    assert read(client.get("/describe").get_data(as_text=True)).scripts == ("/static/unsaved.js",)
    assert read(client.get("/caveats").get_data(as_text=True)).scripts == ()
    _direct(client)
    assert read(client.get("/everything").get_data(as_text=True)).scripts == ("/static/unsaved.js",)
    served = client.get("/static/unsaved.js")
    assert served.status_code == 200
    script = served.get_data(as_text=True)
    assert "beforeunload" in script
    assert "sessionStorage" in script
    assert "/write" not in script, "the guard must not touch the write action"


# 4. A refused answer keeps what was typed ------------------------------------


@pytest.mark.parametrize("mode", ["guided", "direct"])
def test_a_refused_answer_comes_back_with_what_was_ticked_and_typed(
    walk: Walk, walk_sql: Path, mode: str
) -> None:
    """Found by testers: Back after a refusal showed no box ticked and an
    empty "Or type" field.
    """
    _app, client, session = walk(walk_sql)
    if mode == "direct":
        _direct(client)
    key = _append_key(session)
    sent = {"key": key, "kind": "merge", "columns": ["event_id"], "typed": "not_a_column"}
    refused = client.post("/answer", data=sent, follow_redirects=True)
    assert refused.status_code == 400
    assert key not in session.answers

    back = read(refused.get_data(as_text=True)).named_links["back"]
    page = read(client.get(back).get_data(as_text=True))
    merge = [c for c in page.controls if c.get("form") == "/answer"]
    # The controls of the merge option only: they follow its hidden kind.
    kinds = [c.get("value") for c in merge if c.get("name") == "kind"]
    assert "merge" in kinds
    ticked = [
        c.get("value")
        for c in page.inputs
        if c.get("name") == "columns" and "checked" in c and c.get("type") == "checkbox"
    ]
    assert ticked == ["event_id"]
    typed = [c.get("value") for c in page.inputs if c.get("name") == "typed"]
    assert typed.count("not_a_column") == 1

    # One render only: the next look at the question is a fresh one.
    again = read(client.get(urlsplit(back).path).get_data(as_text=True))
    assert "not_a_column" not in [c.get("value") for c in again.inputs]


# 5. One column, one tick -------------------------------------------------------


def test_an_option_that_takes_one_column_offers_radio_buttons(walk: Walk, walk_sql: Path) -> None:
    """Found by testers: the checked merge accepts one column and offered
    checkboxes, then refused two ticks.
    """
    _app, client, session = walk(walk_sql)
    index = [d.key for d in session.view().questions].index(_append_key(session))

    html = client.get(f"/questions/{index}").get_data(as_text=True)
    forms = html.split('<form method="post" action="/answer">')[1:]
    types: dict[str, set[str]] = {}
    for form in forms:
        kind = re.search(r'name="kind" value="([^"]+)"', form)
        assert kind is not None
        types[kind.group(1)] = set(re.findall(r'type="(\w+)" name="columns"', form))
    assert types["merge_checked"] == {"radio"}
    assert types["merge"] == {"checkbox"}


# 6. Rail words name the question --------------------------------------------


def test_the_rail_kind_word_names_the_question_not_an_answer(walk: Walk, walk_sql: Path) -> None:
    """Found by testers: the rail said "append" after the reader answered
    merge.
    """
    _app, client, session = walk(walk_sql)
    answer = {"key": _append_key(session), "kind": "merge_checked", "typed": "event_id"}
    assert client.post("/answer", data=answer).status_code == 302

    nav = client.get("/").get_data(as_text=True).split("<nav>")[1].split("</nav>")[0]
    kinds = re.findall(r'<span class="kind" data-identifier>([^<]+)</span>', nav)
    assert "new rows" in kinds
    assert "append" not in kinds
    assert "merge" not in kinds


# 7. Names a model cannot have, and an empty paste -----------------------------


@pytest.fixture
def unstarted(project_dir: Path, out_dir: Path):  # type: ignore[no-untyped-def]
    from dbtw.web.app import create_app

    source = Source(out=out_dir)
    app = create_app(source)
    app.testing = True
    return app.test_client(), source, project_dir


def _refusal(html: str) -> tuple[str, str]:
    """(the label in front of the refusal, the refusal itself)."""
    block = html.split('<p class="refusal">')[1].split("</p>")[0]
    message = re.search(r"<span data-engine>(.*?)</span>", block, re.S)
    assert message is not None
    label = re.sub(r"<span data-engine>.*?</span>", "", block, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", "", label).split()), message.group(1)


@pytest.mark.parametrize("name", ["a b", "Weekly-Revenue", "1st_week", "café"])
def test_a_name_dbt_cannot_give_a_model_is_refused_and_says_which_names_work(
    unstarted, name: str
) -> None:  # type: ignore[no-untyped-def]
    client, source, project_dir = unstarted

    refused = client.post(
        "/source", data={"project": str(project_dir), "pasted": ONE_APPEND, "name": name}
    )

    assert refused.status_code == 400
    assert source.session is None
    label, message = _refusal(refused.get_data(as_text=True))
    assert label == "File name:"
    assert "letters, digits and underscores" in message
    assert message.endswith(".")
    assert ONE_APPEND.strip() in refused.get_data(as_text=True), "the paste is kept"
    assert f'value="{name}"' in refused.get_data(as_text=True), "the typed name is kept"


@pytest.mark.parametrize("sql", ["-- just a comment\n", "/* nothing */\n", ";\n"])
def test_a_paste_with_no_statement_in_it_is_refused_like_an_empty_one(unstarted, sql: str) -> None:  # type: ignore[no-untyped-def]
    client, source, project_dir = unstarted

    refused = client.post("/source", data={"project": str(project_dir), "pasted": sql, "name": "q"})

    assert refused.status_code == 400
    assert source.session is None


def test_a_refused_empty_paste_keeps_the_previous_conversation(unstarted) -> None:  # type: ignore[no-untyped-def]
    client, source, project_dir = unstarted
    started = client.post(
        "/source", data={"project": str(project_dir), "pasted": ONE_APPEND, "name": "first"}
    )
    assert started.status_code == 302
    held = source.session

    refused = client.post(
        "/source", data={"project": str(project_dir), "pasted": "-- only this\n", "name": "q"}
    )

    assert refused.status_code == 400
    assert source.session is held
    assert held is not None
    assert source.shown() == "first.sql", "the staged source was cleared by the refusal"
    assert [m.name for m in held.view().change.models] == ["stg_revenue_events"]


def test_a_leading_dot_is_refused_for_the_dot_not_for_a_folder(unstarted) -> None:  # type: ignore[no-untyped-def]
    client, _source, project_dir = unstarted

    refused = client.post(
        "/source", data={"project": str(project_dir), "pasted": ONE_APPEND, "name": ".hidden"}
    )

    label, message = _refusal(refused.get_data(as_text=True))
    assert label == "File name:"
    assert "folder" not in message
    assert "dot" in message
    assert message.endswith(".")


def test_the_no_name_refusal_is_labelled_and_reads_as_a_sentence(unstarted) -> None:  # type: ignore[no-untyped-def]
    client, _source, project_dir = unstarted

    refused = client.post(
        "/source", data={"project": str(project_dir), "pasted": ONE_APPEND, "name": " "}
    )

    label, message = _refusal(refused.get_data(as_text=True))
    assert label == "File name:"
    assert message[0].isupper()
    assert message.endswith(".")


def test_the_other_projects_box_says_it_wants_dbt_projects(unstarted) -> None:  # type: ignore[no-untyped-def]
    client, _source, _project_dir = unstarted

    html = client.get("/source").get_data(as_text=True)

    assert '<span class="key">Other dbt projects</span>' in html


# 8. Backticks ----------------------------------------------------------------


def test_a_code_span_in_engine_text_is_shown_as_code(walk: Walk, sql_script) -> None:  # type: ignore[no-untyped-def]
    """Found by testers: the parse reason on the caveats screen showed its
    backticks.
    """
    _app, client, session = walk(sql_script(UNPARSEABLE_AND_APPEND))
    ((_, statement),) = session.view().change.pending
    assert "`--dialect postgres`" in statement.reason

    html = client.get("/caveats").get_data(as_text=True)

    assert "<code>--dialect postgres</code>" in html
    page = read(html)
    shown = [run for run in page.engine if "dialect postgres" in run and "--dialect" in run]
    assert shown
    assert not [run for run in shown if "`" in run]


# 9. A delivered branch that a new write has moved past -------------------------


def _git(root: Path, *arguments: str) -> str:
    done = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _as_git_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "reader@example.invalid")
    _git(root, "config", "user.name", "The Reader")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "the project as it was")


def test_a_new_write_after_delivery_is_offered_on_a_new_branch(
    walk: Walk, walk_sql: Path, project_dir: Path
) -> None:
    """Found by testers: after deliver and push, a changed answer and a new
    write were shown under "Delivered" with the old commit, and there was no
    way to deliver the new write.
    """
    from dbtw.core.deliver import earlier_write

    _as_git_repo(project_dir)
    base = _git(project_dir, "rev-parse", "HEAD")
    _app, client, session = walk(walk_sql)
    assert client.post("/write", follow_redirects=True).status_code == 200
    assert client.post("/deliver", follow_redirects=True).status_code == 200
    first = _git(project_dir, "branch", "--list", "dbtw/*").strip(" *")
    assert first
    # The project is back where the reader had it, so the walk reads it as
    # the reader has it -- not as a project that already holds this write.
    assert _git(project_dir, "branch", "--show-current") == "main"

    answer = {"key": _append_key(session), "kind": "merge_checked", "typed": "event_id"}
    assert client.post("/answer", data=answer).status_code == 302

    # Before the new write, the old result is not shown as current.
    stale = client.get("/written")
    assert stale.status_code == 302
    assert stale.headers["Location"] == "/done"

    shown = client.post("/write", follow_redirects=True)
    assert shown.status_code == 200
    page = read(shown.get_data(as_text=True))
    assert normalised(earlier_write(first)) in {normalised(run) for run in page.engine}
    offered = [c for c in page.inputs if c.get("name") == "branch"]
    assert len(offered) == 1
    second = offered[0]["value"]
    assert second and second != first

    delivered = client.post("/deliver", data={"branch": second}, follow_redirects=True)
    assert delivered.status_code == 200
    assert earlier_write(first) not in delivered.get_data(as_text=True)
    # Branched from where the first delivery started, so the new branch holds
    # the new write and nothing of the old one.
    assert _git(project_dir, "rev-parse", f"{second}~1") == base
    assert _git(project_dir, "branch", "--show-current") == "main"


def test_a_delivery_whose_return_fails_is_shown_as_delivered_and_says_where_it_is(
    walk: Walk, walk_sql: Path, project_dir: Path
) -> None:
    """Review finding: a failed checkout after the commit showed "Not
    delivered", though the branch and its commit existed, and a retry then
    branched from that commit and carried the first conversion.
    """
    _as_git_repo(project_dir)
    base = _git(project_dir, "rev-parse", "HEAD")
    hook = project_dir / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\ntouch .git/index.lock\n", encoding="utf-8")
    hook.chmod(0o755)
    app, client, session = walk(walk_sql)
    assert client.post("/write", follow_redirects=True).status_code == 200

    delivered = client.post("/deliver", follow_redirects=True)

    assert delivered.status_code == 200
    page = read(delivered.get_data(as_text=True))
    assert "Not delivered" not in delivered.get_data(as_text=True)
    first = _git(project_dir, "branch", "--show-current")
    assert first.startswith("dbtw/")
    engine = {normalised(run) for run in page.engine}
    said = [run for run in engine if "could not check out" in run]
    assert said and first in said[0] and "main" in said[0]

    # The reader clears the lock. A changed answer and a new write are then
    # delivered from where the project started, not from the first branch.
    hook.unlink()
    (project_dir / ".git" / "index.lock").unlink()
    answer = {"key": _append_key(session), "kind": "merge_checked", "typed": "event_id"}
    assert client.post("/answer", data=answer).status_code == 302
    assert client.post("/write", follow_redirects=True).status_code == 200
    again = client.post("/deliver", follow_redirects=True)
    assert again.status_code == 200
    (second,) = [
        name
        for name in _git(project_dir, "branch", "--format=%(refname:short)").split()
        if name.startswith("dbtw/") and name != first
    ]
    assert _git(project_dir, "rev-parse", f"{second}~1") == base
    assert _git(project_dir, "branch", "--show-current") == "main"


def test_a_long_refused_answer_does_not_ride_in_the_back_link(walk: Walk, walk_sql: Path) -> None:
    """Review finding: the draft in the Back link had no length bound, so a
    long typed value could make the redirect header too long."""
    _app, client, session = walk(walk_sql)
    key = _append_key(session)
    long = "x" * 5000

    refused = client.post(
        "/answer", data={"key": key, "kind": "merge", "typed": long}, follow_redirects=True
    )

    assert refused.status_code == 400
    back = read(refused.get_data(as_text=True)).named_links["back"]
    assert long not in back
    assert len(back) < 1000
    assert urlsplit(back).query == "", "a draft that is too long is not carried at all"


def test_two_backticks_together_are_text_not_an_empty_code_span() -> None:
    from tests.unit.web.page import as_shown

    from dbtw.web.app import _code_spans

    assert str(_code_spans("a `` b")) == "a `` b"
    assert str(_code_spans("`x` and `` and `y`")) == "<code>x</code> and `` and <code>y</code>"
    assert as_shown("`x` and `` and `y`") == "x and `` and y"


# 10. The mode toggle lands on the matching screen -------------------------------


def test_switching_to_guided_on_the_one_screen_lands_on_the_first_question(
    walk: Walk, walk_sql: Path
) -> None:
    _app, client, _session = walk(walk_sql)
    _direct(client)

    switched = client.post("/mode", data={"mode": "guided", "back": "/everything"})

    assert switched.headers["Location"] == "/questions/0"


def test_switching_mode_on_the_written_page_stays_there(walk: Walk, walk_sql: Path) -> None:
    _app, client, _session = walk(walk_sql)
    written = client.post("/write", follow_redirects=True)
    toggle = [c for c in read(written.get_data(as_text=True)).inputs if c.get("name") == "back"]
    assert [c["value"] for c in toggle] == ["/written"]

    switched = client.post("/mode", data={"mode": "direct", "back": "/written"})

    assert switched.headers["Location"] == "/written"


def test_switching_mode_on_the_refusal_page_lands_on_its_question(
    walk: Walk, walk_sql: Path
) -> None:
    _app, client, session = walk(walk_sql)
    index = [d.key for d in session.view().questions].index(_append_key(session))
    refused = client.post(
        "/answer", data={"key": _append_key(session), "kind": "merge"}, follow_redirects=True
    )
    toggle = [c for c in read(refused.get_data(as_text=True)).inputs if c.get("name") == "back"]
    assert [c["value"] for c in toggle] == ["/refused"]

    switched = client.post("/mode", data={"mode": "direct", "back": "/refused"})

    landed = urlsplit(switched.headers["Location"])
    assert (landed.path, landed.fragment) == ("/everything", f"question-{index}")
    # With the refused answer still on the way back, so its picker refills.
    assert "kind=merge" in landed.query


# 11. The tags box says how it splits ----------------------------------------------


def test_the_tags_box_says_how_tags_are_split(walk: Walk, walk_sql: Path) -> None:
    _app, client, _session = walk(walk_sql)
    label = '<span class="key">Tags (commas or spaces)</span>'
    assert label in client.get("/describe").get_data(as_text=True)
    _direct(client)
    assert label in client.get("/everything").get_data(as_text=True)


# 12. A question this walk does not ask, in direct mode ----------------------------


def test_a_question_this_walk_does_not_ask_is_not_here_in_direct_mode_either(
    walk: Walk, walk_sql: Path
) -> None:
    _app, client, _session = walk(walk_sql)
    guided = client.get("/questions/99")
    _direct(client)

    direct = client.get("/questions/99")

    assert guided.status_code == 404
    assert direct.status_code == 404
    assert "Not here" in direct.get_data(as_text=True)
