"""Writes an assembled ProjectChange to disk. Everything lands under out_dir —
nothing in this slice touches the target dbt project itself. Validation
(does it compile, does it run against a warehouse) is slice 5.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from pathlib import Path

from dbtw.core.assemble import ProjectChange
from dbtw.core.assemble.layers import layer_roles
from dbtw.core.context import ProjectContext, SourceInfo
from dbtw.core.emit.render import render_model, render_sources_yaml
from dbtw.core.emit.report import render_report
from dbtw.core.passes.types import Decision

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

    collisions: tuple[Decision, ...] = ()
    if change.sources:
        sources_rel = (_sources_dir(ctx) / _SOURCES_NAME).as_posix()
        sources_path = _safe_join(out_dir, sources_rel)
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        sources_path.write_text(render_sources_yaml(change.sources), encoding="utf-8")
        written.append(sources_path)
        collisions = _sources_collisions(change, ctx, sources_rel)

    # The report has to answer for every file emit wrote, and the sources file
    # is one the target project may already have its own copy of. The clash is
    # only knowable here, because only here is the path it lands on decided.
    reported = (
        dataclasses.replace(change, decisions=(*change.decisions, *collisions))
        if collisions
        else change
    )

    report_path = _safe_join(out_dir, _REPORT_NAME)
    report_path.write_text(render_report(reported, ctx), encoding="utf-8")
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


def _decision(kind: str, name: str, action: str, reason: str) -> Decision:
    """One Decision about a file emit wrote.

    The key carries no trailing ":<index>", because these answer for a file
    rather than for a statement in the source SQL — the same reason
    `source_file` and the line numbers are empty, so the report prints no
    location instead of pointing at SQL that has nothing to do with the clash.
    """
    return Decision(
        key=f"emit.{kind}.{name}",
        tier=1,
        action=action,
        reason=reason,
        source_file="",
        line_start=0,
        line_end=0,
    )


def _listed(items: list[str]) -> str:
    """`a`, `a and b`, `a, b and c` — a list a person reads."""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _sources_collisions(
    change: ProjectChange, ctx: ProjectContext, sources_rel: str
) -> tuple[Decision, ...]:
    """What the sources.yml just written lands on in the target project.

    `_sources_dir` puts our file beside the project's own declaration rather
    than in a rival file, which is the right placement and is exactly why the
    two can meet. They meet in two ways, and both are silent without this:

    (1) Same path. Copying out_dir over the project replaces that file
        outright, so every declaration in it that ours does not repeat stops
        existing, and the project's own models that referenced it stop
        resolving.
    (2) A different file, a shared source name. Nothing is overwritten, but
        the project then declares one source in two files, which dbt rejects
        as a duplicate.

    Reporting, not merging. A merge would mean rewriting a file dbtw was
    never asked to touch, out of a ProjectContext that keeps source names and
    table names only — descriptions, tests, freshness and columns never
    reached it (`context.reader._collect_sources`), so a merged file built
    from what we know would hand back strictly less than it was given, and
    would do it to the user's own file. Naming the clash cannot damage
    anything, and the report is where the tool already answers for what it
    wrote.
    """
    ours = {(entry.source_name, entry.table) for entry in change.sources}
    our_names = {entry.source_name for entry in change.sources}

    by_file: dict[str, list[SourceInfo]] = {}
    for existing in ctx.existing_sources:
        by_file.setdefault(existing.declared_in, []).append(existing)

    decisions: list[Decision] = []

    at_our_path = by_file.get(sources_rel)
    if at_our_path is not None:
        theirs = sorted({f"{s.source_name}.{s.table}" for s in at_our_path})
        lost = sorted(
            {
                f"{s.source_name}.{s.table}"
                for s in at_our_path
                if (s.source_name, s.table) not in ours
            }
        )
        shared = sorted({s.source_name for s in at_our_path} & our_names)
        if lost:
            replacement = (
                f"{sources_rel} in the target project declares {_listed(lost)}, which this "
                "file does not; copying this output over the project replaces that file, and "
                "those declarations go with it."
            )
        else:
            replacement = (
                f"{sources_rel} in the target project declares {_listed(theirs)}, all of "
                "which this file declares too; copying this output over the project still "
                "replaces that file."
            )
        if shared:
            subject = "the source" if len(shared) == 1 else "the sources"
            verb = "is" if len(shared) == 1 else "are"
            keeping = (
                f"Keeping both means renaming one of them, and {subject} {_listed(shared)} "
                f"{verb} then declared twice in one project, which dbt rejects — put the "
                "entries you need in one file before running dbt."
            )
        else:
            keeping = (
                "The two files declare no source in common, so keeping both is only a matter "
                "of giving one of them a different filename — do that, or put the entries you "
                "need in one file, before running dbt."
            )
        decisions.append(
            _decision(
                "sources_replace",
                sources_rel,
                f"sources.yml lands at {sources_rel}, where this project already declares sources",
                f"{replacement} This file carries source names, schemas and table names only, "
                "so any descriptions, tests or freshness on the project's entries do not "
                f"survive the replacement either. {keeping}",
            )
        )

    for other_file in sorted(by_file):
        if other_file == sources_rel:
            continue
        shared = sorted({s.source_name for s in by_file[other_file]} & our_names)
        if not shared:
            continue
        subject = "the source" if len(shared) == 1 else "the sources"
        verb = "is" if len(shared) == 1 else "are"
        decisions.append(
            _decision(
                "sources_duplicate",
                other_file,
                f"sources.yml lands at {sources_rel}, declaring {subject} {_listed(shared)}, "
                f"which {other_file} already declares",
                f"{other_file} in the target project already declares {subject} "
                f"{_listed(shared)}. With both files in place {subject} {_listed(shared)} "
                f"{verb} declared twice in one project, which dbt rejects — move these "
                f"entries into {other_file}, or move that file's into this one, before "
                "running dbt.",
            )
        )

    return tuple(decisions)
