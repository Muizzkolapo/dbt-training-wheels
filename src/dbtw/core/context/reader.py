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
    SourceInfo,
)

_DBT_DEFAULT_MODEL_PATHS = ("models",)
_PREFIX_RE = re.compile(r"^([a-z]+_)")


def read_project(root: Path | str) -> ProjectContext:
    root = Path(root)
    raw = _load_project_yaml(root)
    detections: list[Detection] = []

    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise NotADbtProjectError(f"dbt_project.yml at {root} has no usable name")
    project_name = name

    models_raw = raw.get("models")
    if models_raw is not None and not isinstance(models_raw, dict):
        raise NotADbtProjectError(f"dbt_project.yml at {root} has a non-mapping 'models' key")
    models_config = (models_raw or {}).get(project_name)

    if "model-paths" in raw:
        raw_model_paths = raw["model-paths"]
        if not isinstance(raw_model_paths, list) or not all(
            isinstance(p, str) for p in raw_model_paths
        ):
            raise NotADbtProjectError(
                f"dbt_project.yml at {root} has a malformed 'model-paths'"
                " (expected a list of strings)"
            )
        model_paths = tuple(raw_model_paths)
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

    vars_raw = raw.get("vars")
    if vars_raw is not None and not isinstance(vars_raw, dict):
        raise NotADbtProjectError(f"dbt_project.yml at {root} has a non-mapping 'vars' key")
    vars_block = vars_raw or {}
    vars_declared = tuple(sorted((str(k), v) for k, v in vars_block.items()))

    models, layer_bases = _collect_models(root, model_paths)
    layers, layer_detections = _build_layers(models, layer_bases, models_config)
    detections.extend(layer_detections)

    found_sources, source_warnings = _collect_sources(root, model_paths)
    detections.extend(source_warnings)

    return ProjectContext(
        project_name=project_name,
        model_paths=model_paths,
        layers=tuple(layers),
        existing_models=tuple(models),
        existing_sources=tuple(found_sources),
        vars_declared=vars_declared,
        detections=tuple(detections),
    )


def _load_project_yaml(root: Path) -> dict[str, Any]:
    project_file = root / "dbt_project.yml"
    if not project_file.is_file():
        raise NotADbtProjectError(f"no dbt_project.yml at {root}")
    try:
        loaded = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        raise NotADbtProjectError(f"dbt_project.yml at {root} could not be parsed: {exc}") from exc
    if not isinstance(loaded, dict):
        raise NotADbtProjectError(f"dbt_project.yml at {root} is not a mapping")
    return loaded


def _collect_models(
    root: Path, model_paths: tuple[str, ...]
) -> tuple[list[ModelInfo], dict[str, str]]:
    models: list[ModelInfo] = []
    # layer name -> the model-path it was found under, so _build_layers can
    # derive <model-path>/<layer> without depending on how deep any one
    # model happens to be nested inside that layer.
    layer_bases: dict[str, str] = {}
    for mp in model_paths:
        base = root / mp
        if not base.is_dir():
            continue
        for sql in sorted(base.rglob("*.sql")):
            rel_to_base = sql.relative_to(base)
            layer = rel_to_base.parts[0] if len(rel_to_base.parts) > 1 else "root"
            layer_bases.setdefault(layer, mp)
            models.append(
                ModelInfo(
                    name=sql.stem,
                    path=sql.relative_to(root).as_posix(),
                    layer=layer,
                )
            )
    return models, layer_bases


def _collect_sources(
    root: Path, model_paths: tuple[str, ...]
) -> tuple[list[SourceInfo], list[Detection]]:
    sources: list[SourceInfo] = []
    warnings: list[Detection] = []
    for mp in model_paths:
        base = root / mp
        if not base.is_dir():
            continue
        for yml in sorted([*base.rglob("*.yml"), *base.rglob("*.yaml")]):
            rel = yml.relative_to(root).as_posix()
            try:
                loaded = yaml.safe_load(yml.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
                # Demo lesson: broken YAML (and files that aren't even valid
                # text) exist in real projects. Skip the file, but record the
                # skip — never silently, never fatally.
                warnings.append(
                    Detection(
                        key="warning.unparseable_yaml",
                        status="undetermined",
                        value=None,
                        evidence=f"skipped {rel}: {exc}",
                    )
                )
                continue
            if not isinstance(loaded, dict):
                continue
            sources_raw = loaded.get("sources")
            if not isinstance(sources_raw, list):
                continue
            for src in sources_raw:
                if not isinstance(src, dict) or "name" not in src:
                    continue
                tables_raw = src.get("tables")
                if not isinstance(tables_raw, list):
                    continue
                for table in tables_raw:
                    if isinstance(table, dict) and "name" in table:
                        sources.append(
                            SourceInfo(
                                source_name=str(src["name"]),
                                table=str(table["name"]),
                                declared_in=rel,
                            )
                        )
    return sources, warnings


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
    models: list[ModelInfo],
    layer_bases: dict[str, str],
    models_config: dict[str, Any] | None,
) -> tuple[list[LayerInfo], list[Detection]]:
    groups: dict[str, list[ModelInfo]] = defaultdict(list)
    for m in models:
        groups[m.layer].append(m)
    layers: list[LayerInfo] = []
    detections: list[Detection] = []
    for layer_name in sorted(groups):
        members = groups[layer_name]
        # A layer's dir path: model-path root for "root", else <model-path>/<layer>.
        # Derived from the model-path the layer was found under — not from any one
        # member's file path, which may sit arbitrarily deep inside the layer dir.
        base = layer_bases[layer_name]
        dir_path = base if layer_name == "root" else f"{base}/{layer_name}"
        det = _detect_prefix([m.name for m in members], layer_name, dir_path)
        detections.append(det)

        parts = () if layer_name == "root" else (layer_name,)
        mat = _materialization_for(models_config, parts)
        mat_key = f"layer.{layer_name}.materialization"
        if mat is not None:
            detections.append(
                Detection(
                    key=mat_key,
                    status="detected",
                    value=mat,
                    evidence=f"models config in dbt_project.yml for {dir_path}",
                )
            )
        else:
            detections.append(
                Detection(
                    key=mat_key,
                    status="undetermined",
                    value=None,
                    evidence=f"no materialized setting found for {dir_path} in dbt_project.yml",
                )
            )

        layers.append(
            LayerInfo(
                name=layer_name,
                path=dir_path,
                prefix=det.value if det.status == "detected" else None,
                materialization=mat,
            )
        )
    return layers, detections


def _materialization_for(
    models_config: dict[str, Any] | None, layer_dir_parts: tuple[str, ...]
) -> str | None:
    """Nearest-ancestor materialization for a dir under the models config tree.

    layer_dir_parts is the path under the model-path root, () for root.
    Accepts both '+materialized' and 'materialized' at every level.
    """
    if models_config is None:
        return None
    node: Any = models_config
    found = _mat_at(node)
    for part in layer_dir_parts:
        child = node.get(part) if isinstance(node, dict) else None
        if not isinstance(child, dict):
            break
        node = child
        found = _mat_at(node) or found
    return found


def _mat_at(node: Any) -> str | None:
    if not isinstance(node, dict):
        return None
    value = node.get("+materialized", node.get("materialized"))
    return str(value) if value is not None else None
