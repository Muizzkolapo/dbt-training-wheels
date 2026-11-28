"""The web write names a pasted file by its name, not by its staging path.

`dbtw web` stages a paste in a temporary directory. The report that the walk
writes is the report that delivery commits into the reader's project, so a
temporary path in it means nothing to anyone who reads it later.
"""

from __future__ import annotations

from pathlib import Path


def test_the_web_write_names_a_pasted_file_by_its_name(project_dir: Path, out_dir: Path) -> None:
    from dbtw.web import Source
    from dbtw.web.app import create_app

    source = Source(out=out_dir, remember=False)
    app = create_app(source)
    app.testing = True
    client = app.test_client()
    client.post(
        "/source",
        data={
            "project": str(project_dir),
            "pasted": "INSERT INTO revenue_events SELECT order_id, amount FROM stg_orders;\n",
            "name": "nightly",
        },
    )

    assert client.post("/write", follow_redirects=True).status_code == 200

    report = (out_dir / "CONVERSION_REPORT.md").read_text(encoding="utf-8")
    assert "(nightly.sql:1)" in report
    assert "dbtw-source" not in report
