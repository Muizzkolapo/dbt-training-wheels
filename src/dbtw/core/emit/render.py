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

    # After materialization and the incremental pair, before grants: this is
    # the order dbt's own docs put them in, and a config block is read top to
    # bottom by someone checking it against what they asked for.
    if model.tags:
        args.append(f"tags={list(model.tags)!r}")

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


def render_schema_yaml(model: str, tests: Sequence[SchemaTest] = (), description: str = "") -> str:
    """The .yml content declaring the dbt tests an answer asked for.

    The model is named rather than derived, and that changed deliberately.
    It used to be read off the `SchemaTest` entries, on the grounds that a
    separate name parameter could disagree with them -- true, and the reason
    the disagreement is still refused below. What broke the premise is that a
    .yml now has content that carries no model name of its own: a description
    with no test. The name has to come from the caller because there are
    files this writes where nothing else knows it. `tests` naming more than one model is a
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
    naming the same (model, column) would render two `columns:` entries under
    one name, which dbt rejects, so the merge would be needed the moment two
    could exist. Two cannot: `SchemaTest.test` is `Literal["unique"]`,
    mirroring `Option.declares_test`, and `assemble` records at most one test
    per model -- one per branch of its own loop over its own models, and a
    model takes one branch. Widen that Literal, or record a second test for
    one model, and this needs the merge first.
    """
    if not tests and not description.strip():
        return ""
    named = {t.model for t in tests}
    if named - {model}:
        raise ValueError(
            f"render_schema_yaml renders one model's schema per call; it was given "
            f"{model!r} and tests naming {sorted(named)}. Group change.tests by model "
            "before calling this."
        )
    entry: dict[str, Any] = {"name": model}
    if description.strip():
        entry["description"] = description.strip()
    if tests:
        entry["columns"] = [{"name": t.column, "tests": [t.test]} for t in tests]
    doc: dict[str, Any] = {
        "version": 2,
        "models": [entry],
    }
    return yaml.dump(doc, Dumper=_IndentedDumper, sort_keys=False, default_flow_style=False)
