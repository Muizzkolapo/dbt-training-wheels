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

Table references are not yet rewritten as ref() or source() calls. Tests,
documentation blocks, and exposures were not generated. Review the model
bodies and the decisions above before treating any of this as final.\
"""


def render_report(change: ProjectChange, ctx: ProjectContext) -> str:
    sections = [
        _render_summary(change),
        _render_conventions(ctx),
        _render_models(change),
        _render_sources(change),
        _render_decisions(change),
        _render_pending(change),
        _NOT_DONE_YET,
    ]
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
