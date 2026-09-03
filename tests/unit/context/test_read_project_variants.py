from pathlib import Path

from dbtw.core.context import read_project

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def test_nested_config_and_bare_materialized_key():
    ctx = read_project(FIXTURES / "nested_config")
    marts = next(layer for layer in ctx.layers if layer.name == "marts")
    # models/marts/finance carries +materialized: table; the marts LAYER itself
    # has no dir-level setting, so it inherits the project default (bare key).
    assert marts.materialization == "view"


def test_no_models_config_is_undetermined():
    ctx = read_project(FIXTURES / "no_conventions")
    det = {d.key: d for d in ctx.detections}["layer.root.materialization"]
    assert det.status == "undetermined"
    assert det.value is None


def test_plus_materialized_wins_over_bare_on_same_node(tmp_path):
    (tmp_path / "dbt_project.yml").write_text(
        "name: both_keys\n"
        "config-version: 2\n"
        "models:\n"
        "  both_keys:\n"
        "    materialized: view\n"
        "    +materialized: table\n"
    )
    models = tmp_path / "models"
    models.mkdir()
    (models / "a.sql").write_text("select 1 as id")
    (models / "b.sql").write_text("select 1 as id")
    ctx = read_project(tmp_path)
    root = next(layer for layer in ctx.layers if layer.name == "root")
    assert root.materialization == "table"
