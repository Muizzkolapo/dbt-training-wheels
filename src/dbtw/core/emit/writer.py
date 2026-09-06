"""Writes an assembled ProjectChange to disk. Everything lands under out_dir —
nothing in this slice touches the target dbt project itself. Validation
(does it compile, does it run against a warehouse) is slice 5.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from pathlib import Path

from dbtw.core.assemble import AssembledModel, ProjectChange
from dbtw.core.assemble.layers import layer_roles
from dbtw.core.context import ProjectContext
from dbtw.core.emit.render import render_model, render_schema_yaml, render_sources_yaml
from dbtw.core.emit.report import render_report
from dbtw.core.passes.types import Decision, SchemaTest

_REPORT_NAME = "CONVERSION_REPORT.md"
_SOURCES_NAME = "sources.yml"

# The name our sources file takes when the target project already uses
# sources.yml in the directory ours belongs in. It keeps the "sources" stem, so
# anyone scanning the directory reads it as a source file and it sorts
# immediately beside the project's own; the suffix names the tool that wrote
# it, so a reader can tell at a glance which of the two is theirs. dbt's own
# style guide prefixes source files with an underscore (_project__sources.yml),
# which sorts them to the top of the directory -- away from the file this one
# exists to sit next to.
_ALT_SOURCES_NAME = "sources_dbtw.yml"


@dataclasses.dataclass(frozen=True, slots=True)
class EmitResult:
    """What emit() wrote, and what it recorded about writing it.

    `decisions` are emit's own -- the ones no earlier stage could make,
    because they are about where a file landed on disk. They are already in
    the report; they are returned as well so a caller with a terminal can say
    one exists in the Decision's own words, rather than composing a second
    sentence about the same choice that can drift from the first.
    """

    paths: tuple[Path, ...]
    decisions: tuple[Decision, ...]


class DuplicateSourceEntryError(ValueError):
    """A proposed SourceEntry repeats a (source, table) the target project
    already declares.

    Unreachable through the pipeline: `assemble._source_entries` skips every
    such pair before any of them reach `change.sources`, and that disjointness
    is the whole reason our sources file and the project's can stand side by
    side. A ValueError subclass like UnsafeOutputPathError, but unlike that
    one it is NOT a usage error and is deliberately absent from the CLI's
    _USAGE_ERRORS: an input cannot produce it, so reaching it means a dbtw
    bug, and a bug should surface as one.
    """


class UnsafeOutputPathError(ValueError):
    """A model's path would resolve outside out_dir. Input-driven — the model
    name came from the source SQL (e.g. a quoted identifier like
    "../../escape") — never a dbtw bug, so callers should treat it as a
    usage error, not a crash. Subclasses ValueError: emit() already raised
    plain ValueError here, and existing callers/tests that catch ValueError
    must keep working unchanged.
    """


def emit(change: ProjectChange, ctx: ProjectContext, out_dir: Path) -> EmitResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    # Decided before anything is written: _sources_placement refuses a change
    # whose entries repeat the project's own declarations, and a refusal that
    # fires after three model files have landed has already left half an
    # out_dir behind for the caller to clean up.
    sources_rel = ""
    placement: tuple[Decision, ...] = ()
    if change.sources:
        sources_rel, placement = _sources_placement(change, ctx)

    # Grouped once, up front, rather than filtered per model in the loop
    # below: a SchemaTest naming a model absent from this dict is exactly the
    # caller bug render_schema_yaml refuses loudly if two models' tests ever
    # reach one call -- this grouping is what keeps that from happening.
    tests_by_model: dict[str, list[SchemaTest]] = {}
    for test in change.tests:
        tests_by_model.setdefault(test.model, []).append(test)

    for model in change.models:
        model_path = _safe_join(out_dir, model.path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text(render_model(model), encoding="utf-8")
        written.append(model_path)

        model_tests = tests_by_model.get(model.name)
        if model_tests:
            schema_path = _safe_join(out_dir, _schema_yaml_rel(model))
            schema_path.parent.mkdir(parents=True, exist_ok=True)
            schema_path.write_text(render_schema_yaml(tuple(model_tests)), encoding="utf-8")
            written.append(schema_path)

    if change.sources:
        sources_path = _safe_join(out_dir, sources_rel)
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        sources_path.write_text(render_sources_yaml(change.sources), encoding="utf-8")
        written.append(sources_path)

    # The report has to answer for every file emit wrote, and where the
    # sources file landed is only knowable here, because only here is the
    # path it lands on decided.
    reported = (
        dataclasses.replace(change, decisions=(*change.decisions, *placement))
        if placement
        else change
    )

    report_path = _safe_join(out_dir, _REPORT_NAME)
    report_path.write_text(render_report(reported, ctx), encoding="utf-8")
    written.append(report_path)

    return EmitResult(paths=tuple(written), decisions=placement)


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


def _schema_yaml_rel(model: AssembledModel) -> str:
    """The project-relative path a model's schema .yml lands at: `model.path`
    with `.sql` swapped for `.yml`, so the file sits in the same directory as
    the model it tests, under the model's own final name.

    This file class gets no `_sources_placement`-style collision Decision of
    its own, and that is a considered choice, not an oversight. Sources.yml's
    landing name is a fixed constant ("sources.yml") that nothing upstream
    vouches for -- any project could already have a file at that exact path,
    which is the whole reason `_sources_placement` exists. A schema .yml's
    landing name is different in kind: it is *derived from* `model.name`, the
    model's own final name, which `assemble._final_name` already resolves
    against the target project before this module ever sees it. If a project
    file already sits at this exact path, that file sits at the model's own
    name one suffix removed -- and a project file at a NEW model's name is
    precisely what `assemble` already calls a "collision" and records as its
    own Decision (assembler.py: `existing_by_name.get(final_name)`, that
    module's own `_decision("collision", ...)`). There is no separate clash
    for this function to discover that the model-naming pass has not already
    reported; inventing one here would just restate that Decision in a
    second, easier to drift, sentence. (Proven for the fixture projects in
    test_writer.py::test_a_models_schema_yml_never_lands_on_a_declared_sources_file.)
    """
    return Path(model.path).with_suffix(".yml").as_posix()


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
    location instead of pointing at SQL that has nothing to do with placement.
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


def _sources_placement(
    change: ProjectChange, ctx: ProjectContext
) -> tuple[str, tuple[Decision, ...]]:
    """The project-relative path this change's sources file lands at, and the
    Decision recording it when that is not the ordinary name.

    `_sources_dir` puts our sources in the directory the project declares its
    own in, so our entries sit beside theirs rather than in a rival file at
    the far end of the tree. That is the right directory and it is exactly why
    the two files can meet: in a project that declares sources in
    `<dir>/sources.yml`, our `<dir>/sources.yml` has their file's name, and a
    user copying out_dir over the project replaces it — every declaration in
    it gone, and the models that referenced them stop resolving.

    So we don't take that name. Ours lands as `sources_dbtw.yml` in the same
    directory, and copying the whole of out_dir across adds a file instead of
    replacing one. dbt reads both: its duplicate check is per (source, table),
    not per source name, so two files may declare source `raw` as long as they
    declare different tables — which is guaranteed here, since
    `assemble._source_entries` skips every (source, table) the project already
    declares before any of them reach `change.sources`. The user may still
    merge the two by hand, and the Decision says so; it is a preference about
    how many files they want, not something dbt requires.

    Reporting rather than merging for them. `ProjectContext` keeps source and
    table names only — descriptions, tests, freshness and columns never reach
    it (`context.reader._collect_sources`) — so a merged file built from what
    we know would hand back strictly less than it was given, and would do it
    to the user's own file. A separate file cannot damage anything.

    The alternate name is bumped if the project declares sources under it too
    (a previous run's output, copied in): a fixed second name that is already
    taken is the same overwrite one step along.
    """
    directory = _sources_dir(ctx)
    preferred = (directory / _SOURCES_NAME).as_posix()
    declared_at = {existing.declared_in for existing in ctx.existing_sources}
    if preferred not in declared_at:
        return preferred, ()

    landing = (directory / _ALT_SOURCES_NAME).as_posix()
    bump = 1
    while landing in declared_at:
        bump += 1
        landing = (directory / f"sources_dbtw_{bump}.yml").as_posix()

    at_preferred = [s for s in ctx.existing_sources if s.declared_in == preferred]
    theirs = {(s.source_name, s.table) for s in at_preferred}
    ours = {(entry.source_name, entry.table) for entry in change.sources}
    # Not a defensive check — the disjointness is what makes the two files
    # legal side by side, and it is `_source_entries`' guarantee, not ours. If
    # it ever stops holding, the Decision below starts telling users a pair of
    # files is fine when dbt would reject it, which is the failure this whole
    # rewrite exists to remove. A raise rather than an assert for exactly that
    # reason: `python -O` strips an assert, and what is left is emit writing
    # the self-contradicting Decision to disk and returning as if all were
    # well — the defect, restored, in the build most likely to be a container.
    overlap = sorted(f"{name}.{table}" for name, table in theirs & ours)
    if overlap:
        raise DuplicateSourceEntryError(
            "assemble._source_entries skips every (source, table) the target project "
            "already declares, so a proposed entry can never repeat one of theirs; "
            f"{_listed(overlap)} did. Refusing rather than writing a placement "
            "Decision that says two files stand side by side, beside a file that "
            "declares a table the project already declares."
        )

    named_theirs = _listed(sorted(f"{name}.{table}" for name, table in theirs))
    named_ours = _listed(sorted(f"{name}.{table}" for name, table in ours))
    return landing, (
        _decision(
            "sources_placed",
            landing,
            f"wrote this conversion's sources as {landing}, not {preferred}, "
            "which the target project already uses",
            f"{preferred} in the target project declares {named_theirs}; this "
            f"conversion declares {named_ours}. Writing ours at that path would "
            "replace their file and take their declarations with it, so ours is a "
            "separate file and nothing of the project's is touched. dbt reads both — "
            "its duplicate check is per source and table, so two files may declare "
            "the same source name as long as the tables differ, and this conversion "
            "skips every table the project already declares. Copy both across as they "
            f"are, or merge our entries into {preferred} by hand if you would rather "
            "keep one file.",
        ),
    )
