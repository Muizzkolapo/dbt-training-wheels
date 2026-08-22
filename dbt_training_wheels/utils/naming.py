"""Naming utilities for dbt model standardization."""

from __future__ import annotations

import re
from typing import Any


def normalize_identifier(value: str | None, case_style: str = "snake_case", separator: str = "_") -> str:
    """Normalize an identifier based on naming config."""
    if value is None:
        return ""

    raw = str(value).strip()
    if not raw:
        return ""

    # Preserve double underscores used for unique naming (collision handling)
    # If the name contains __, it's already been uniquely named, so skip normalization
    if "__" in raw:
        return raw.lower()

    # Insert underscores between camelCase/PascalCase boundaries
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw)
    # Replace non-alphanumeric/non-underscore characters with underscores
    # (preserves existing underscores including double underscores)
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", normalized)
    # Collapse 3+ consecutive underscores to single underscore
    # (preserves double underscores used for unique naming in collision handling)
    normalized = re.sub(r"_{3,}", "_", normalized).strip("_")

    words = [w for w in normalized.split("_") if w]
    if not words:
        return ""

    case_style = case_style or "snake_case"
    separator = separator if separator is not None else "_"

    if case_style == "lowercase":
        return separator.join(words).lower()
    if case_style == "uppercase":
        return separator.join(words).upper()
    if case_style == "camelCase":
        return words[0].lower() + "".join(word.title() for word in words[1:])
    if case_style == "PascalCase":
        return "".join(word.title() for word in words)

    # Default to snake_case
    return "_".join(words).lower()


def apply_prefix(name: str, prefix: str) -> str:
    """Apply prefix if not already present."""
    if not prefix:
        return name
    return name if name.startswith(prefix) else f"{prefix}{name}"


def apply_suffix(name: str, suffix: str) -> str:
    """Apply suffix if not already present."""
    if not suffix:
        return name
    return name if name.endswith(suffix) else f"{name}{suffix}"


def build_model_name(
    base_name: str,
    prefix: str = "",
    suffix: str = "",
    case_style: str = "snake_case",
    separator: str = "_",
) -> str:
    """Build a model name using naming config (case style + prefix/suffix)."""
    normalized = normalize_identifier(base_name, case_style=case_style, separator=separator)
    normalized = apply_prefix(normalized, prefix)
    normalized = apply_suffix(normalized, suffix)
    return normalized


def get_case_style_and_separator(
    config: Any | None = None,
    project_name: str | None = None,
    naming: Any | None = None,
) -> tuple[str, str]:
    """Resolve case style and separator from naming/config."""
    case_style = "snake_case"
    separator = "_"

    if naming is not None:
        case_style = naming.case_style or case_style
        separator = naming.separator
        return case_style, separator

    if config and project_name and getattr(config, "projects", None) and project_name in config.projects:
        project_config = config.projects[project_name]
        if project_config.dbt_config and project_config.dbt_config.naming:
            project_naming = project_config.dbt_config.naming
            case_style = project_naming.case_style or case_style
            separator = project_naming.separator
            return case_style, separator

    if config and config.naming:
        case_style = config.naming.case_style or case_style
        separator = config.naming.separator

    return case_style, separator
