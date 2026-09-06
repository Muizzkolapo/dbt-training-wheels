from pathlib import Path

import pytest

from dbtw.core.context import NotADbtProjectError, read_project

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


def test_malformed_source_shapes_do_not_crash(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: p\nconfig-version: 2\n")
    models = tmp_path / "models"
    models.mkdir()
    (models / "scalar_sources.yml").write_text("version: 2\nsources: 2\n")
    (models / "scalar_tables.yml").write_text(
        'version: 2\nsources:\n  - name: raw\n    tables: "x"\n'
    )
    (models / "good.yml").write_text(
        "version: 2\nsources:\n  - name: raw\n    tables:\n      - name: events\n"
    )
    ctx = read_project(tmp_path)
    assert {(s.source_name, s.table) for s in ctx.existing_sources} == {("raw", "events")}


def test_non_utf8_model_yaml_is_recorded_not_fatal(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: p\nconfig-version: 2\n")
    models = tmp_path / "models"
    models.mkdir()
    (models / "bad_encoding.yml").write_bytes(b"name: caf\xe9\n")
    ctx = read_project(tmp_path)
    warn = next(d for d in ctx.detections if d.key == "warning.unparseable_yaml")
    assert warn.status == "undetermined"
    assert "models/bad_encoding.yml" in warn.evidence


def test_nested_layer_path_is_model_path_plus_layer_name():
    ctx = read_project(FIXTURES / "nested_config")
    marts = next(layer for layer in ctx.layers if layer.name == "marts")
    assert marts.path == "models/marts"


def _project(root, extra_config: str = "") -> None:
    (root / "dbt_project.yml").write_text(f"name: p\nconfig-version: 2\n{extra_config}")
    models = root / "models"
    models.mkdir()
    (models / "a.sql").write_text("select 1 as id")


def test_sources_declared_under_seed_and_snapshot_paths_are_collected(tmp_path):
    """dbt parses `sources:` out of every YAML file in the project, not just
    the ones under model-paths — a seeds/schema.yml declaring a source is a
    real declaration, and a conversion that cannot see it re-proposes the same
    (source, table) and hands the user a duplicate dbt refuses to parse.
    """
    _project(tmp_path)
    for directory, table in (("seeds", "orders"), ("snapshots", "customers")):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "schema.yml").write_text(
            f"version: 2\nsources:\n  - name: raw\n    tables:\n      - name: {table}\n"
        )
    ctx = read_project(tmp_path)
    assert {(s.source_name, s.table, s.declared_in) for s in ctx.existing_sources} == {
        ("raw", "orders", "seeds/schema.yml"),
        ("raw", "customers", "snapshots/schema.yml"),
    }


def test_configured_seed_and_snapshot_paths_are_honoured_over_the_defaults(tmp_path):
    _project(tmp_path, 'seed-paths: ["data"]\nsnapshot-paths: ["snaps"]\n')
    for directory in ("data", "snaps", "seeds"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "schema.yml").write_text(
            f"version: 2\nsources:\n  - name: {directory}\n    tables:\n      - name: t\n"
        )
    ctx = read_project(tmp_path)
    # "seeds" is not a seed-path in this project, so nothing in it is read.
    assert {s.source_name for s in ctx.existing_sources} == {"data", "snaps"}


def test_a_directory_configured_twice_declares_its_sources_once(tmp_path):
    """seed-paths pointing at a model-path is legal and reads the same files
    twice. A source counted twice skews `emit._sources_dir`'s most-common
    declaring file, which decides where our own sources land.
    """
    _project(tmp_path, 'seed-paths: ["models"]\n')
    (tmp_path / "models" / "schema.yml").write_text(
        "version: 2\nsources:\n  - name: raw\n    tables:\n      - name: orders\n"
    )
    ctx = read_project(tmp_path)
    assert len(ctx.existing_sources) == 1


def test_malformed_seed_paths_is_reported_like_malformed_model_paths(tmp_path):
    _project(tmp_path, "seed-paths: 3\n")
    with pytest.raises(NotADbtProjectError, match="malformed 'seed-paths'"):
        read_project(tmp_path)
