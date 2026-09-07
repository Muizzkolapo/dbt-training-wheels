"""Renders assembled models and sources into dbt project text. No I/O."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import yaml

from dbtw.core.assemble import AssembledModel, SourceEntry
from dbtw.core.passes import SchemaTest


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

    if model.incremental_strategy is not None:
        args.append(f"incremental_strategy={model.incremental_strategy!r}")

    if model.unique_key:
        key: str | list[str] = (
            model.unique_key[0] if len(model.unique_key) == 1 else list(model.unique_key)
        )
        args.append(f"unique_key={key!r}")

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


def render_schema_yaml(tests: Sequence[SchemaTest]) -> str:
    """The .yml content declaring the dbt tests an answer asked for.

    One argument, not `(model_name, tests)`: a separate name parameter could
    disagree with what `tests` actually names, which is a representable lie
    this function should not be able to tell. The model name is derived from
    the entries themselves instead. `tests` naming more than one model is a
    caller bug -- `writer.emit()` groups `change.tests` by model before ever
    calling this -- so it is refused loudly rather than silently rendered
    under whichever model happened to come first.

    `tests:` rather than `data_tests:`. dbt 1.8 introduced the second spelling
    and kept the first working; older versions know only the first. Emitting
    `tests:` is therefore the spelling every version in use accepts, and it is
    the one to change if a floor above 1.8 is ever set. Checked against a real
    dbt (fusion 2.0 preview): the file parses with no deprecation warning and
    the test registers as a node (`unique_<model>_<column>`), rather than
    being read and ignored.

    One column entry per test, not merged by column, the way
    `render_sources_yaml` merges tables under one source. Two `SchemaTest`s
    naming the same column would need that kind of merge into a single
    `tests: [...]` list, but it cannot happen today: `SchemaTest.test` is
    typed `Literal["unique"]` (mirroring `Option.declares_test`), the only
    test this engine can offer, so no two entries can ever differ only in
    which test they declare for one (model, column) pair. Widen that Literal
    when a second test is offered, and build the merge then.
    """
    if not tests:
        return ""
    models = {t.model for t in tests}
    if len(models) > 1:
        raise ValueError(
            "render_schema_yaml renders one model's tests per call, and its "
            f"model name is derived from the entries themselves; got tests naming "
            f"{sorted(models)}. Group change.tests by model before calling this."
        )
    (model_name,) = models
    doc: dict[str, Any] = {
        "version": 2,
        "models": [
            {
                "name": model_name,
                "columns": [{"name": t.column, "tests": [t.test]} for t in tests],
            }
        ],
    }
    return yaml.dump(doc, Dumper=_IndentedDumper, sort_keys=False, default_flow_style=False)
