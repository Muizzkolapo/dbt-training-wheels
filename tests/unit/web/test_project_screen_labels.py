"""The /project screen leads each convention with its label, not its key.

Testers read `PROJECT.MODEL_PATHS` and `layer.intermediate.prefix` on this
screen. Those keys are this tool's own names. The label comes from
`dbtw.core.context`, and the key stays as secondary text.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from tests.unit.web.conftest import Walk

from dbtw.core.context import read_project
from dbtw.core.context.types import Detection, ProjectContext


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


def test_backticked_spans_in_a_conventions_text_render_as_code(
    walk: Walk, walk_sql: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Engine text marks code with backticks, and every other screen renders
    each span as `<code>`. This screen showed the backticks themselves.

    The project read is the real one, with one convention added whose label,
    reading and evidence each carry a span -- the three fields a reader reads
    as prose on this screen.
    """
    import dbtw.web.app as web_app

    real = web_app.read_project

    def read_with_spans(root: Path) -> ProjectContext:
        ctx = real(root)
        spanned = Detection(
            key="layer.staging.prefix",
            label="Prefix of models in `models/odd`",
            status="detected",
            value="`odd_`",
            evidence="from `3` of 3 models",
        )
        return replace(ctx, detections=(*ctx.detections, spanned))

    monkeypatch.setattr(web_app, "read_project", read_with_spans)
    _app, client, _session = walk(walk_sql)

    page = client.get("/project").get_data(as_text=True)

    assert "<code>models/odd</code>" in page
    assert "<code>odd_</code>" in page
    assert "<code>3</code>" in page
    assert "`" not in page


def test_a_lone_backtick_in_a_conventions_text_stays_as_text(
    walk: Walk, walk_sql: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backtick with no partner opens no span. A folder name can hold one,
    and it is text the reader must see as it is."""
    import dbtw.web.app as web_app

    real = web_app.read_project

    def read_with_lone(root: Path) -> ProjectContext:
        ctx = real(root)
        lone = Detection(
            key="layer.odd.prefix",
            label="Prefix of models in models/o`dd",
            status="undetermined",
            value=None,
            evidence="only one model in models/o`dd",
        )
        return replace(ctx, detections=(*ctx.detections, lone))

    monkeypatch.setattr(web_app, "read_project", read_with_lone)
    _app, client, _session = walk(walk_sql)

    page = client.get("/project").get_data(as_text=True)

    assert "only one model in models/o`dd" in page
    assert "<code>" not in page.split('class="detections"', 1)[1]
