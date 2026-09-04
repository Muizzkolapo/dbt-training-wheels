"""Renders the human-facing conversion report. No I/O — text in, text out."""

from __future__ import annotations

import sqlglot
from sqlglot.errors import SqlglotError

from dbtw.core.assemble import ProjectChange
from dbtw.core.context import ProjectContext
from dbtw.core.naming import is_atomic_sql
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


def _yaml_double_quoted(text: str) -> str:
    """Wrap `text` as a YAML double-quoted scalar whose *content* is `text`
    verbatim, backslash/double-quote escaped so the scalar stays well-formed.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parenthesized_if_compound(default_sql: str, dialect: str | None) -> str:
    """A compound default (`1 + 2`) must reach `var()` with the same
    defensive parens `--inline-vars` would give it — `naming.is_atomic_sql`
    is the one shared rule both paths use, so they can never disagree on
    what counts as "needs parens" (FINDING 7: `{{ var('n') }} * 3` and an
    inlined `(1 + 2) * 3` must compute the same thing for the same `n`).
    An unparseable default_sql (extraction should never actually produce
    one, since it always comes from a node that did parse) degrades to the
    unwrapped text rather than crashing report rendering.
    """
    try:
        node = sqlglot.parse_one(default_sql, read=dialect)
    except SqlglotError:
        return default_sql
    if is_atomic_sql(node):
        return default_sql
    return f"({default_sql})"


def _render_vars(change: ProjectChange) -> str:
    # default_sql is the raw SQL literal text (e.g. `'2024-01-01'`, quotes
    # included for a string default). It must reach `var()` byte-identical to
    # what --inline-vars would splice into the SQL — that's the whole point
    # of keeping a variable instead of inlining it. Interpolating it bare
    # (`{name}: {default_sql}`) puts it in YAML's *plain* scalar syntax,
    # where a leading `'` starts a *single-quoted* scalar and YAML strips
    # those quotes on load: `start_date: '2024-01-01'` loads as the bare
    # string `2024-01-01`, and dbt renders that unquoted into the compiled
    # SQL — `WHERE order_date >= 2024-01-01` is integer arithmetic, not a
    # date comparison (FINDING 1, proven with a real `dbt compile`).
    #
    # The fix: wrap default_sql in a YAML *double*-quoted scalar, so its
    # content — the SQL literal, quotes and all — survives the YAML round
    # trip intact and var() renders exactly what --inline-vars would inline.
    # See test_vars_block_stays_parseable_yaml_for_awkward_defaults.
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
            value = _parenthesized_if_compound(v.default_sql, change.dialect)
            lines.append(f"  {v.name}: {_yaml_double_quoted(value)}")
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
