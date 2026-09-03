"""Data shapes for the target-project context. No I/O, no logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DetectionStatus = Literal["detected", "undetermined"]


class NotADbtProjectError(Exception):
    """The given path has no dbt_project.yml — it is not a dbt project."""


@dataclass(frozen=True, slots=True)
class Detection:
    """One detected (or undetectable) convention, with its provenance."""

    key: str  # e.g. "layer.staging.prefix", "project.model_paths"
    status: DetectionStatus
    value: str | None  # None when undetermined, or when 'no prefix' is the finding
    evidence: str  # human-readable: what was concluded, from what


@dataclass(frozen=True, slots=True)
class LayerInfo:
    name: str  # directory name, or "root" for models at a model-path root
    path: str  # project-root-relative posix dir, e.g. "models/staging"
    prefix: str | None  # e.g. "stg_"; None = no prefix or undetermined
    materialization: str | None  # from dbt_project.yml config; None if unset


@dataclass(frozen=True, slots=True)
class ModelInfo:
    name: str  # file stem, e.g. "stg_customers"
    path: str  # project-root-relative posix path to the .sql file
    layer: str  # LayerInfo.name of the layer it sits in


@dataclass(frozen=True, slots=True)
class SourceInfo:
    source_name: str  # the sources: entry name, e.g. "raw"
    table: str  # one table under it, e.g. "customers"
    declared_in: str  # project-root-relative posix path of the YAML file


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """Everything the pipeline knows about the target dbt project. Read-only."""

    project_name: str
    model_paths: tuple[str, ...]
    layers: tuple[LayerInfo, ...]
    existing_models: tuple[ModelInfo, ...]
    existing_sources: tuple[SourceInfo, ...]
    vars_declared: tuple[tuple[str, object], ...]  # sorted (name, default) pairs
    detections: tuple[Detection, ...]
