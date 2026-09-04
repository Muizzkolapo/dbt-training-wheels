"""Renders the human-facing conversion report. No I/O — text in, text out."""

from __future__ import annotations

from dbtw.core.assemble import ProjectChange
from dbtw.core.context import ProjectContext
from dbtw.core.passes.types import Decision

_NOT_DONE_YET = """\
## Not done yet

This report describes a mechanical conversion only. No validation has run —
dbt was not invoked, nothing was compiled, and nothing was run against a
warehouse.

Table references and script variables have been rewritten as ref(), source(),
and var() calls. Incremental models, temporary tables, and UPDATE or other
multi-statement rewrites remain deferred. Tests, documentation blocks, and
exposures were not generated. Review the model bodies and the decisions above
before treating any of this as final.\
"""


def render_report(change: ProjectChange, ctx: ProjectContext) -> str:
    sections = [
        _render_summary(change),
        _render_conventions(ctx),
        _render_models(change),
        _render_sources(change),
    ]
    if change.variables:
        sections.append(_render_vars(change))
    sections.extend(
        [
            _render_decisions(change),
            _render_pending(change),
            _NOT_DONE_YET,
        ]
    )
    return "\n\n".join(sections) + "\n"


def _render_summary(change: ProjectChange) -> str:
    dialect = change.dialect if change.dialect is not None else "not specified"
    lines = [
        "## Summary",
        "",
        f"- **Project**: {change.project_name}",
        f"- **Models**: {len(change.models)}",
        f"- **Sources**: {len(change.sources)}",
        f"- **Pending statements**: {len(change.pending)}",
        f"- **Dialect**: {dialect}",
    ]
    return "\n".join(lines)


def _render_conventions(ctx: ProjectContext) -> str:
    lines = ["## Your project's conventions", ""]
    if not ctx.detections:
        lines.append("No conventions were detected.")
        return "\n".join(lines)
    for d in ctx.detections:
        value = d.value if d.value is not None else "undetermined"
        lines.append(f"- **{d.key}**: {value} — {d.evidence}")
    return "\n".join(lines)


def _render_models(change: ProjectChange) -> str:
    lines = ["## Models", ""]
    if not change.models:
        lines.append("No models were produced.")
        return "\n".join(lines)
    lines.append("| Model | Layer | Materialization | Depends on |")
    lines.append("| --- | --- | --- | --- |")
    for m in change.models:
        mat = m.materialization if m.materialization is not None else "(layer default)"
        deps = ", ".join(m.depends_on) if m.depends_on else "—"
        lines.append(f"| {m.name} | {m.layer} | {mat} | {deps} |")
    return "\n".join(lines)


def _render_sources(change: ProjectChange) -> str:
    lines = ["## Sources", ""]
    if not change.sources:
        lines.append("None to declare.")
        return "\n".join(lines)
    for s in change.sources:
        lines.append(f"- **{s.source_name}**.{s.table} (schema: {s.schema})")
    return "\n".join(lines)


def _render_vars(change: ProjectChange) -> str:
    # default_sql is interpolated into a YAML value unescaped. This is safe
    # only because sqlglot's canonical .sql() output quotes string literals
    # with single quotes and doubles embedded quotes ('O''Brien'), which is
    # exactly YAML's single-quoted scalar escaping — and renders structured
    # literals (lists, structs) as function calls rather than raw [ / { {
    # syntax. If variable extraction ever emits SQL literals another way,
    # re-verify this invariant (see test_vars_block_stays_parseable_yaml_for_awkward_defaults).
    lines = [
        "## Add to your dbt_project.yml",
        "",
        "These are the script variables the conversion turned into `var()` calls. "
        "This is a fragment to merge into your project's existing `dbt_project.yml`, "
        "not a replacement for it.",
        "",
        "```yaml",
        "vars:",
    ]
    for v in change.variables:
        if v.default_sql is None:
            lines.append(f"  {v.name}:  # no default in the source; set one")
        else:
            lines.append(f"  {v.name}: {v.default_sql}")
    lines.append("```")
    return "\n".join(lines)


def _render_decisions(change: ProjectChange) -> str:
    lines = ["## Decisions", ""]
    if not change.decisions:
        lines.append("No decisions were recorded.")
        return "\n".join(lines)

    by_tier: dict[int, list[Decision]] = {}
    for d in change.decisions:
        by_tier.setdefault(d.tier, []).append(d)

    tier_blocks: list[str] = []
    for tier in sorted(by_tier):
        block_lines = [f"### Tier {tier}", ""]
        for d in by_tier[tier]:
            location = f" ({d.source_file}:{d.line_start})" if d.source_file else ""
            block_lines.append(f"- **{d.action}** — {d.reason}{location}")
            if d.question:
                line = f"  - Question: {d.question}  Chose: {d.chosen}"
                if d.alternatives:
                    line += f"  (alternatives: {', '.join(d.alternatives)})"
                block_lines.append(line)
        tier_blocks.append("\n".join(block_lines))
    lines.append("\n\n".join(tier_blocks))
    return "\n".join(lines)


def _render_pending(change: ProjectChange) -> str:
    lines = ["## Still pending", ""]
    if not change.pending:
        lines.append("Nothing — every statement was handled.")
        return "\n".join(lines)
    for _, stmt in change.pending:
        first_line = stmt.raw.text.splitlines()[0] if stmt.raw.text else ""
        lines.append(f"- **{stmt.kind}** — {first_line}")
    return "\n".join(lines)
