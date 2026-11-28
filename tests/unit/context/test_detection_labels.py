"""Each convention has a label that a reader can read.

The report and the walk's /project screen showed raw keys such as
`layer.intermediate.prefix: undetermined` and `PROJECT.MODEL_PATHS`. Those
keys are the names this tool uses inside. A reader who has never used dbt
cannot read them. Each Detection now carries a label in plain words, and a
value in plain words when nothing was found. The key stays visible as
secondary text, so a reader can still match a row to the key.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.emit.test_report import _change

from dbtw.core.context import Detection, read_project
from dbtw.core.emit.report import render_report

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _by_key(project: str) -> dict[str, Detection]:
    return {d.key: d for d in read_project(FIXTURES / project).detections}


def test_each_detection_has_a_label_in_plain_words() -> None:
    detections = _by_key("three_layers")

    assert detections["project.model_paths"].label == "Folders that hold models"
    assert detections["layer.intermediate.prefix"].label == "Prefix of intermediate models"
    assert detections["layer.marts.materialization"].label == "Materialization of marts models"
    for detection in detections.values():
        assert detection.label[0].isupper(), detection.label
        assert detection.key not in detection.label


def test_a_layer_at_the_root_of_a_model_folder_is_named_by_its_folder(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: p\nconfig-version: 2\n")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.sql").write_text("SELECT 1")

    detections = {d.key: d for d in read_project(tmp_path).detections}

    assert detections["layer.root.prefix"].label == "Prefix of models directly in models"
    assert "root" not in detections["layer.root.materialization"].label


def test_a_file_that_could_not_be_read_has_a_label(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: p\nconfig-version: 2\n")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "broken.yml").write_text("sources: [unclosed\n  bad: :\n")

    (warn,) = [d for d in read_project(tmp_path).detections if d.key == "warning.unparseable_yaml"]

    assert warn.label == "File that this tool could not read"
    assert warn.reading == "not found"


def test_the_reading_is_plain_words_when_there_is_no_value() -> None:
    undetermined = Detection(
        key="layer.marts.prefix",
        label="Prefix of marts models",
        status="undetermined",
        value=None,
        evidence="only one model in models/marts",
    )
    no_prefix = Detection(
        key="layer.marts.prefix",
        label="Prefix of marts models",
        status="detected",
        value=None,
        evidence="no prefix — 0 of 3 models in models/marts use one",
    )
    found = Detection(
        key="layer.marts.prefix",
        label="Prefix of marts models",
        status="detected",
        value="fct_",
        evidence="fct_ — from 3 of 3 models in models/marts",
    )

    assert undetermined.reading == "not found"
    assert no_prefix.reading == "none"
    assert found.reading == "fct_"


def test_the_report_leads_with_the_label_and_keeps_the_key_as_code() -> None:
    report = render_report(_change(), read_project(FIXTURES / "jaffle_shop"))
    section = report.split("## Your project's conventions")[1].split("\n## ")[0]

    assert "undetermined" not in section
    assert "- **Prefix of staging models**: " in section
    assert "`layer.staging.prefix`" in section
    assert "**layer." not in section
    assert "**project." not in section
