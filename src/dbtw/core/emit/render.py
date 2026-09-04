"""Renders assembled models and sources into dbt project text. No I/O."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import yaml

from dbtw.core.assemble import AssembledModel, SourceEntry


class _IndentedDumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def render_model(model: AssembledModel) -> str:
    blocks: list[str] = []

    if model.leading_comments:
        blocks.append("\n".join(f"-- {comment}" for comment in model.leading_comments))

    config_args = _config_args(model)
    if config_args:
        args = ",\n".join(f"    {arg}" for arg in config_args)
        blocks.append(f"{{{{ config(\n{args}\n) }}}}")

    blocks.append(model.body)

    return "\n\n".join(blocks).rstrip("\n") + "\n"


def _config_args(model: AssembledModel) -> list[str]:
    args: list[str] = []

    if model.materialization is not None:
        args.append(f"materialized={model.materialization!r}")

    if model.grants:
        merged: dict[str, list[str]] = {}
        for privilege, principals in model.grants:
            merged.setdefault(privilege.lower(), []).extend(principals)
        grants = {key: merged[key] for key in sorted(merged)}
        args.append(f"grants={grants!r}")

    return args


def render_sources_yaml(sources: Sequence[SourceEntry]) -> str:
    if not sources:
        return ""

    schema_by_source: dict[str, str] = {}
    tables_by_source: dict[str, set[str]] = defaultdict(set)
    for entry in sources:
        schema_by_source.setdefault(entry.source_name, entry.schema)
        tables_by_source[entry.source_name].add(entry.table)

    doc: dict[str, Any] = {
        "version": 2,
        "sources": [
            {
                "name": name,
                "schema": schema_by_source[name],
                "tables": [{"name": table} for table in sorted(tables_by_source[name])],
            }
            for name in sorted(tables_by_source)
        ],
    }

    return yaml.dump(doc, Dumper=_IndentedDumper, sort_keys=False, default_flow_style=False)
