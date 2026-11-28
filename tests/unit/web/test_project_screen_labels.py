"""The /project screen leads each convention with its label, not its key.

Testers read `PROJECT.MODEL_PATHS` and `layer.intermediate.prefix` on this
screen. Those keys are this tool's own names. The label comes from
`dbtw.core.context`, and the key stays as secondary text.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.web.conftest import Walk

from dbtw.core.context import read_project


def test_each_convention_shows_its_label_and_its_reading(
    walk: Walk, walk_sql: Path, project_dir: Path
) -> None:
    _app, client, _session = walk(walk_sql)

    page = client.get("/project").get_data(as_text=True)

    detections = read_project(project_dir).detections
    assert detections
    for detection in detections:
        assert detection.label in page, detection.label
    assert "Not determined" not in page
    labels = [page.index(d.label) for d in detections]
    keys = [page.index(d.key) for d in detections]
    assert all(label < key for label, key in zip(labels, keys, strict=True)), "label first"
