from pathlib import Path

from dbtw.core.context import read_project

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _layer(ctx, name):
    return next(layer for layer in ctx.layers if layer.name == name)


def test_all_models_found_and_placed():
    ctx = read_project(FIXTURES / "jaffle_shop")
    by_name = {m.name: m for m in ctx.existing_models}
    assert set(by_name) == {
        "customers",
        "orders",
        "stg_customers",
        "stg_orders",
        "stg_payments",
    }
    assert by_name["stg_orders"].layer == "staging"
    assert by_name["stg_orders"].path == "models/staging/stg_orders.sql"
    assert by_name["customers"].layer == "root"


def test_files_outside_model_paths_are_excluded():
    ctx = read_project(FIXTURES / "jaffle_shop")
    assert "notes" not in {m.name for m in ctx.existing_models}


def test_staging_prefix_detected_with_evidence():
    ctx = read_project(FIXTURES / "jaffle_shop")
    assert _layer(ctx, "staging").prefix == "stg_"
    det = {d.key: d for d in ctx.detections}["layer.staging.prefix"]
    assert det.status == "detected"
    assert det.value == "stg_"
    assert "3 of 3" in det.evidence and "models/staging" in det.evidence


def test_root_layer_has_no_prefix_as_a_detected_finding():
    ctx = read_project(FIXTURES / "jaffle_shop")
    assert _layer(ctx, "root").prefix is None
    det = {d.key: d for d in ctx.detections}["layer.root.prefix"]
    assert det.status == "detected"
    assert det.value is None
