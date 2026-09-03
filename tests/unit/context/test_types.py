import dataclasses

import pytest

from dbtw.core.context import Detection, LayerInfo, ProjectContext


def test_context_is_immutable():
    ctx = ProjectContext(
        project_name="p",
        model_paths=("models",),
        layers=(),
        existing_models=(),
        existing_sources=(),
        vars_declared=(),
        detections=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.project_name = "other"  # type: ignore[misc]


def test_detection_records_provenance():
    d = Detection(
        key="layer.staging.prefix",
        status="detected",
        value="stg_",
        evidence="stg_ — from 3 of 3 models in models/staging",
    )
    assert d.status == "detected"
    assert "models/staging" in d.evidence


def test_layer_info_allows_absent_conventions():
    layer = LayerInfo(name="root", path="models", prefix=None, materialization=None)
    assert layer.prefix is None
