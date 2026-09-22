"""The walk when the reader has named another project.

A cross-project answer changes which references a conversion has -- and, less
obviously, it changes which questions the *pristine* run asks, because the
question only exists while the table is still a source. The screens that ask
"what did this one answer produce" rebuild the session minus one answer, and
that rebuild has to carry everything the walk carries or a held answer lands
against a run that never asked it.

Found by walking the whole flow: answer the cross-project question, open a
later question screen, 500.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dbtw.web import Session, Source

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"

# revenue_events reads dim_customers (which core_platform models) and is
# incremental; revenue_daily reads revenue_events and is the layer fork. So
# the walk asks three questions, and answering the cross-project one changes
# the set the other two are resolved against.
SQL = (
    "INSERT INTO revenue_events SELECT c.name, o.amount "
    "FROM analytics.dim_customers AS c JOIN analytics.orders AS o ON o.cid = c.customer_id;\n"
    "INSERT INTO revenue_daily SELECT name, amount FROM revenue_events;\n"
)


@pytest.fixture
def walk_with_other(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    from dbtw.web.app import create_app

    project = tmp_path_factory.mktemp("proj") / "three_layers"
    shutil.copytree(FIXTURES / "three_layers", project)
    sql = tmp_path_factory.mktemp("sql") / "in.sql"
    sql.write_text(SQL, encoding="utf-8")
    other = FIXTURES / "core_platform"

    session = Session(project=project, sql=sql, elsewhere=(other,))
    source = Source(
        out=tmp_path_factory.mktemp("out") / "o",
        project=project,
        elsewhere=(other,),
        session=session,
    )
    app = create_app(source)
    app.testing = True
    return app, app.test_client(), session


def _cross_key(session: Session) -> str:
    return next(d.key for d in session.view().questions if d.key.startswith("assemble.cross_ref."))


def _append_key(session: Session) -> str:
    return next(d.key for d in session.view().questions if ".append." in d.key)


def test_an_answered_question_screen_renders_with_a_cross_project_answer_held(
    walk_with_other,
) -> None:
    """The crash, reproduced. `_produced_by` runs only for a question whose
    own answer is held, and it rebuilds the session minus that one answer to
    ask what the answer produced. That rebuild dropped the other projects, so
    the still-held cross-project answer landed against a run that no longer
    asked it -- straight through the route as a 500.
    """
    app, client, session = walk_with_other
    append = _append_key(session)
    cross = _cross_key(session)
    assert client.post(
        "/answer", data={"key": append, "kind": "merge_checked", "typed": "name"}
    ).status_code in (302, 400)
    # Answer it whichever way the model allows; the point is that it is held.
    if append not in session.answers:
        assert client.post("/answer", data={"key": append, "kind": "merge"}).status_code == 302
    assert client.post("/answer", data={"key": cross, "kind": "cross_ref"}).status_code == 302

    # The append question's own screen: its answer is held, so `_produced_by`
    # runs, and the cross-project answer is held alongside.
    append_index = next(i for i, d in enumerate(session.view().questions) if d.key == append)
    assert client.get(f"/questions/{append_index}").status_code == 200

    for index in range(len(session.view().questions)):
        assert client.get(f"/questions/{index}").status_code == 200, index
    for url in ("/", "/project", "/describe", "/caveats", "/changed", "/files", "/done"):
        assert client.get(url).status_code == 200, url


def test_the_cross_project_answer_is_carried_into_the_written_files(walk_with_other) -> None:
    """And it actually applies: the body reads the other project's model."""
    app, client, session = walk_with_other
    cross = _cross_key(session)
    assert client.post("/answer", data={"key": cross, "kind": "cross_ref"}).status_code == 302
    assert client.post("/write").status_code == 200

    bodies = " ".join(m.body for m in session.view().change.models)
    assert "ref('core_platform', 'dim_customers')" in bodies
