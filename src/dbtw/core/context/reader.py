"""Builds a ProjectContext by statically reading a target dbt project.

Reads dbt_project.yml and walks model-paths. Never parses SQL contents —
conventions come from filenames, directories, and YAML only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dbtw.core.context.types import (
    Detection,
    NotADbtProjectError,
    ProjectContext,
)

_DBT_DEFAULT_MODEL_PATHS = ("models",)


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

    return ProjectContext(
        project_name=project_name,
        model_paths=model_paths,
        layers=(),
        existing_models=(),
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
