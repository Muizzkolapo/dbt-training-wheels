"""Builds a ProjectContext by statically reading a target dbt project.

Reads dbt_project.yml and walks model-paths. Never parses SQL contents —
conventions come from filenames, directories, and YAML only.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from dbtw.core.context.types import (
    Detection,
    LayerInfo,
    ModelInfo,
    NotADbtProjectError,
    ProjectContext,
)

_DBT_DEFAULT_MODEL_PATHS = ("models",)
_PREFIX_RE = re.compile(r"^([a-z]+_)")


def read_project(root: Path | str) -> ProjectContext:
    root = Path(root)
    raw = _load_project_yaml(root)
    detections: list[Detection] = []

    project_name = str(raw.get("name", ""))

    if "model-paths" in raw:
        model_paths = tuple(str(p) for p in raw["model-paths"])
        detections.append(
            Detection(
                key="project.model_paths",
                status="detected",
                value=", ".join(model_paths),
                evidence="model-paths in dbt_project.yml",
            )
        )
    else:
        model_paths = _DBT_DEFAULT_MODEL_PATHS
        detections.append(
            Detection(
                key="project.model_paths",
                status="detected",
                value="models",
                evidence="dbt default ('models') — model-paths key absent",
            )
        )

    vars_block = raw.get("vars") or {}
    vars_declared = tuple(sorted((str(k), v) for k, v in vars_block.items()))

    models = _collect_models(root, model_paths)
    layers, layer_detections = _build_layers(models, model_paths)
    detections.extend(layer_detections)

    return ProjectContext(
        project_name=project_name,
        model_paths=model_paths,
        layers=tuple(layers),
        existing_models=tuple(models),
        existing_sources=(),
        vars_declared=vars_declared,
        detections=tuple(detections),
    )


def _load_project_yaml(root: Path) -> dict[str, Any]:
    project_file = root / "dbt_project.yml"
    if not project_file.is_file():
        raise NotADbtProjectError(f"no dbt_project.yml at {root}")
    try:
        loaded = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise NotADbtProjectError(f"dbt_project.yml at {root} could not be parsed: {exc}") from exc
    if not isinstance(loaded, dict):
        raise NotADbtProjectError(f"dbt_project.yml at {root} is not a mapping")
    return loaded


def _collect_models(root: Path, model_paths: tuple[str, ...]) -> list[ModelInfo]:
    models: list[ModelInfo] = []
    for mp in model_paths:
        base = root / mp
        if not base.is_dir():
            continue
        for sql in sorted(base.rglob("*.sql")):
            rel_to_base = sql.relative_to(base)
            layer = rel_to_base.parts[0] if len(rel_to_base.parts) > 1 else "root"
            models.append(
                ModelInfo(
                    name=sql.stem,
                    path=sql.relative_to(root).as_posix(),
                    layer=layer,
                )
            )
    return models


def _detect_prefix(stems: list[str], layer_name: str, layer_path: str) -> Detection:
    """One Detection for a layer's naming prefix, per the rule in the RFC (§7).

    key uses the layer NAME ("root", "staging"); evidence cites the dir path.
    """
    key = f"layer.{layer_name}.prefix"
    if len(stems) == 1:
        return Detection(
            key=key,
            status="undetermined",
            value=None,
            evidence=f"only one model in {layer_path} — not enough to infer a prefix",
        )
    tokens = {m.group(1) if (m := _PREFIX_RE.match(s)) else None for s in stems}
    if tokens == {None}:
        return Detection(
            key=key,
            status="detected",
            value=None,
            evidence=f"no prefix — 0 of {len(stems)} models in {layer_path} use one",
        )
    if len(tokens) == 1:
        (prefix,) = tokens
        return Detection(
            key=key,
            status="detected",
            value=prefix,
            evidence=f"{prefix} — from {len(stems)} of {len(stems)} models in {layer_path}",
        )
    return Detection(
        key=key,
        status="undetermined",
        value=None,
        evidence=f"mixed prefixes in {layer_path} ({', '.join(sorted(stems))})",
    )


def _build_layers(
    models: list[ModelInfo], model_paths: tuple[str, ...]
) -> tuple[list[LayerInfo], list[Detection]]:
    groups: dict[str, list[ModelInfo]] = defaultdict(list)
    for m in models:
        groups[m.layer].append(m)
    layers: list[LayerInfo] = []
    detections: list[Detection] = []
    for layer_name in sorted(groups):
        members = groups[layer_name]
        # A layer's dir path: model-path root for "root", else <model-path>/<dir>.
        first_path = members[0].path
        dir_path = first_path.rsplit("/", 1)[0]
        det = _detect_prefix([m.name for m in members], layer_name, dir_path)
        detections.append(det)
        layers.append(
            LayerInfo(
                name=layer_name,
                path=dir_path,
                prefix=det.value if det.status == "detected" else None,
                materialization=None,
            )
        )
    return layers, detections
