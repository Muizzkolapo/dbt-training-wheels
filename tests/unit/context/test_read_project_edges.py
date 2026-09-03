from pathlib import Path

import pytest

from dbtw.core.context import NotADbtProjectError, read_project

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def test_missing_dbt_project_yml_raises(tmp_path):
    with pytest.raises(NotADbtProjectError):
        read_project(tmp_path)


def test_project_name_and_model_paths_from_yaml():
    ctx = read_project(FIXTURES / "jaffle_shop")
    assert ctx.project_name == "jaffle_shop"
    assert ctx.model_paths == ("models",)


def test_multiple_model_paths():
    ctx = read_project(FIXTURES / "with_sources")
    assert ctx.model_paths == ("models", "extra_models")


def test_vars_are_read_and_sorted():
    ctx = read_project(FIXTURES / "with_sources")
    assert ctx.vars_declared == (("region", "eu"), ("start_date", "2020-01-01"))


def test_absent_vars_is_empty():
    ctx = read_project(FIXTURES / "jaffle_shop")
    assert ctx.vars_declared == ()


def test_model_paths_default_recorded_when_key_absent(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: bare\nconfig-version: 2\n")
    ctx = read_project(tmp_path)
    assert ctx.model_paths == ("models",)
    det = {d.key: d for d in ctx.detections}["project.model_paths"]
    assert det.status == "detected"
    assert "default" in det.evidence


def test_malformed_dbt_project_yml_raises_with_reason(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: [unclosed\n  bad: :\n")
    with pytest.raises(NotADbtProjectError, match="could not be parsed"):
        read_project(tmp_path)
