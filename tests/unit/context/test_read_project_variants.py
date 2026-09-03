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


def test_sources_are_collected_with_declaring_file():
    ctx = read_project(FIXTURES / "with_sources")
    pairs = {(s.source_name, s.table) for s in ctx.existing_sources}
    assert pairs == {("raw", "customers"), ("raw", "orders")}
    assert all(s.declared_in == "models/staging/sources.yml" for s in ctx.existing_sources)


def test_non_source_yaml_is_ignored():
    ctx = read_project(FIXTURES / "jaffle_shop")  # schema.yml has models:, no sources:
    assert ctx.existing_sources == ()


def test_unparseable_yaml_is_recorded_not_fatal(tmp_path):
    """Demo lesson: real projects contain broken YAML. Skip the file, but
    record the skip as a Detection — never silently, never fatally."""
    (tmp_path / "dbt_project.yml").write_text("name: p\nconfig-version: 2\n")
    models = tmp_path / "models"
    models.mkdir()
    (models / "broken.yml").write_text("sources: [unclosed\n  bad: :\n")
    (models / "good.yml").write_text(
        "version: 2\nsources:\n  - name: raw\n    tables:\n      - name: events\n"
    )
    ctx = read_project(tmp_path)
    assert {(s.source_name, s.table) for s in ctx.existing_sources} == {("raw", "events")}
    warn = next(d for d in ctx.detections if d.key == "warning.unparseable_yaml")
    assert warn.status == "undetermined"
    assert "models/broken.yml" in warn.evidence
