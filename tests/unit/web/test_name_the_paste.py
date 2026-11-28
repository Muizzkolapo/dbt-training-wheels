"""A pasted query is named by the reader, not by this tool.

Found by driving the entry screen in a browser. Pasted text was staged as
`pasted.sql`, and a bare SELECT takes its model's name from its file -- so the
persona's first use, pasting the one SELECT she runs every week, built a model
called `pasted`, and the Decision explaining lesson 1 said "this query is in
pasted.sql, so the model is pasted". The naming rule, taught with a name the
reader never chose.

In dbt every model is a file with a name. Asking for that name where the
query is pasted is the lesson, done rather than described.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.web.helpers import ONE_APPEND

from dbtw.web import Source

WEEKLY = "SELECT customer_id, SUM(amount) AS total FROM raw.orders GROUP BY customer_id\n"


@pytest.fixture
def unstarted(project_dir: Path, out_dir: Path):  # type: ignore[no-untyped-def]
    from dbtw.web.app import create_app

    source = Source(out=out_dir)
    app = create_app(source)
    app.testing = True
    return app.test_client(), source, project_dir


def _paste(client, project_dir: Path, sql: str, name: str):  # type: ignore[no-untyped-def]
    return client.post("/source", data={"project": str(project_dir), "pasted": sql, "name": name})


@pytest.mark.parametrize("name", ["weekly_revenue", "weekly_revenue.sql", "  weekly_revenue  "])
def test_a_pasted_query_becomes_a_model_named_what_the_reader_called_it(
    unstarted, name: str
) -> None:  # type: ignore[no-untyped-def]
    client, source, project_dir = unstarted

    assert _paste(client, project_dir, WEEKLY, name).status_code == 302

    assert source.session is not None
    assert [m.name for m in source.session.current().models] == ["stg_weekly_revenue"]


def test_a_paste_with_no_name_is_refused_and_says_why(unstarted) -> None:  # type: ignore[no-untyped-def]
    client, source, project_dir = unstarted

    refused = _paste(client, project_dir, WEEKLY, "   ")

    assert refused.status_code == 400
    assert source.session is None
    body = refused.get_data(as_text=True)
    assert "takes its name from its file" in body
    assert WEEKLY.strip() in body, "the pasted query is still in the box to retry with"


@pytest.mark.parametrize("name", ["../escape", "sub/dir", ".hidden", ".sql"])
def test_a_name_that_is_not_one_plain_file_name_is_refused(unstarted, name: str) -> None:  # type: ignore[no-untyped-def]
    client, source, project_dir = unstarted

    refused = _paste(client, project_dir, WEEKLY, name)

    assert refused.status_code == 400
    assert source.session is None


def test_uploaded_files_need_no_name_because_they_have_one(unstarted, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import io

    client, source, project_dir = unstarted

    response = client.post(
        "/source",
        data={"project": str(project_dir), "files": (io.BytesIO(WEEKLY.encode()), "weekly.sql")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert source.session is not None
    assert [m.name for m in source.session.current().models] == ["stg_weekly"]


def test_the_start_screen_names_the_files_brought_not_where_they_were_staged(unstarted) -> None:  # type: ignore[no-untyped-def]
    client, source, project_dir = unstarted
    assert _paste(client, project_dir, ONE_APPEND, "nightly").status_code == 302

    start = client.get("/").get_data(as_text=True)

    assert "nightly.sql" in start
    assert "dbtw-source-" not in start


def test_the_bar_shows_no_project_box_before_a_project_is_named(unstarted) -> None:  # type: ignore[no-untyped-def]
    client, _source, _project_dir = unstarted

    entry = client.get("/source").get_data(as_text=True)

    assert 'class="project"' not in entry


def test_a_paste_named_like_an_uploaded_file_is_refused_rather_than_replacing_it(
    unstarted,
) -> None:  # type: ignore[no-untyped-def]
    """Two files under one name is one file on disk. Either the upload or
    the paste would vanish, with nothing on the screen saying which.
    """
    import io

    client, source, project_dir = unstarted

    refused = client.post(
        "/source",
        data={
            "project": str(project_dir),
            "pasted": WEEKLY,
            "name": "weekly",
            "files": (io.BytesIO(ONE_APPEND.encode()), "weekly.sql"),
        },
        content_type="multipart/form-data",
    )

    assert refused.status_code == 400
    assert source.session is None
    assert "weekly.sql" in refused.get_data(as_text=True)


def test_a_name_with_a_nul_byte_is_refused_on_the_screen(unstarted) -> None:  # type: ignore[no-untyped-def]
    client, source, project_dir = unstarted

    refused = _paste(client, project_dir, WEEKLY, "a\x00b")

    assert refused.status_code == 400
    assert source.session is None
