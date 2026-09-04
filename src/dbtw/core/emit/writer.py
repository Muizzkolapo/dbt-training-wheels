"""Writes an assembled ProjectChange to disk. Everything lands under out_dir —
nothing in this slice touches the target dbt project itself. Validation
(does it compile, does it run against a warehouse) is slice 5.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from dbtw.core.assemble import ProjectChange
from dbtw.core.assemble.layers import layer_roles
from dbtw.core.context import ProjectContext
from dbtw.core.emit.render import render_model, render_sources_yaml
from dbtw.core.emit.report import render_report

_REPORT_NAME = "CONVERSION_REPORT.md"
_SOURCES_NAME = "sources.yml"


class UnsafeOutputPathError(ValueError):
    """A model's path would resolve outside out_dir. Input-driven — the model
    name came from the source SQL (e.g. a quoted identifier like
    "../../escape") — never a dbtw bug, so callers should treat it as a
    usage error, not a crash. Subclasses ValueError: emit() already raised
    plain ValueError here, and existing callers/tests that catch ValueError
    must keep working unchanged.
    """


def emit(change: ProjectChange, ctx: ProjectContext, out_dir: Path) -> tuple[Path, ...]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for model in change.models:
        model_path = _safe_join(out_dir, model.path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text(render_model(model), encoding="utf-8")
        written.append(model_path)

    if change.sources:
        sources_path = _safe_join(out_dir, _sources_dir(ctx) / _SOURCES_NAME)
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        sources_path.write_text(render_sources_yaml(change.sources), encoding="utf-8")
        written.append(sources_path)

    report_path = _safe_join(out_dir, _REPORT_NAME)
    report_path.write_text(render_report(change, ctx), encoding="utf-8")
    written.append(report_path)

    return tuple(written)


def _safe_join(out_dir: Path, relative: str | Path) -> Path:
    """out_dir / relative, refusing to write anywhere outside out_dir.

    pathlib silently drops the left operand when the right is absolute
    (Path('/tmp/out') / '/etc/passwd' -> '/etc/passwd'). Nothing upstream is
    expected to hand emit() an absolute or path-escaping AssembledModel.path
    today, but "writes ONLY under out_dir" is a hard constraint and this is
    the last line of defense before anything touches disk.
    """
    target = out_dir / relative
    out_root = out_dir.resolve()
    resolved = target.resolve()
    if resolved != out_root and out_root not in resolved.parents:
        raise UnsafeOutputPathError(
            f"refusing to write outside out_dir: {relative!r} would resolve to {resolved}"
        )
    return target


def _sources_dir(ctx: ProjectContext) -> Path:
    """Where sources.yml belongs, following the target project's own layout.

    In priority order: (1) beside the file the project already declares
    sources in — the most common declared_in, when more than one exists —
    so our entries land next to theirs instead of in a competing second
    file; (2) the staging role layer's path; (3) the first configured
    model-path; (4) "models". This rule exists because the real dbt-labs
    jaffle_shop declares sources at models/sources.yml, at the model-path
    root, NOT under models/staging/ — writing there would have produced two
    rival source files in one project.
    """
    if ctx.existing_sources:
        declared_in = [s.declared_in for s in ctx.existing_sources]
        most_common, _ = Counter(declared_in).most_common(1)[0]
        return Path(most_common).parent

    staging = layer_roles(ctx)["staging"]
    if staging is not None:
        return Path(staging.path)

    if ctx.model_paths:
        return Path(ctx.model_paths[0])

    return Path("models")
