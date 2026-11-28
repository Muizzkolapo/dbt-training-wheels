"""Writes an assembled ProjectChange to disk. Everything lands under out_dir —
nothing in this slice touches the target dbt project itself. Validation
(does it compile, does it run against a warehouse) is slice 5.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn

from dbtw.core.assemble import AssembledModel, ProjectChange
from dbtw.core.assemble.layers import layer_roles
from dbtw.core.context import ProjectContext
from dbtw.core.emit.render import render_model, render_schema_yaml, render_sources_yaml
from dbtw.core.emit.report import render_report
from dbtw.core.passes.types import Decision, SchemaTest
from dbtw.core.progress import NOTHING_EXPLAINED, Progress

# The report's filename, exported because two other packages need to find
# the file `emit` wrote: the CLI prints its path, and the web records which
# words it explained by reading it back. Each kept its own copy of this
# string until then, which is two places for one name to drift.
REPORT_NAME = "CONVERSION_REPORT.md"
_SOURCES_NAME = "sources.yml"

# The record of which files the latest write into an out_dir produced, kept
# in that out_dir beside the report. Exported because delivery reads it: what
# "Put on a branch" copies into the reader's project is the files the latest
# write produced, not whatever else the directory happens to hold.
#
# It exists because a second write into the same out_dir has to know what the
# first one left there. Without it, a model an answer moved from one layer to
# another stayed in the layer it left -- two models with one name, which dbt
# refuses to parse -- and a model the script stopped building stayed as well.
# The directory listing cannot answer the question: the out_dir is the
# reader's directory too, and a file this tool did not write is never one it
# removes. A dot-file, so a reader browsing the conversion does not mistake
# it for part of it; never in `EmitResult.paths`, so no screen lists it.
WRITE_RECORD_NAME = ".dbtw-written.json"
_RECORD_FORMAT = 1

# The name our sources file takes when the target project already uses
# sources.yml in the directory ours belongs in. It keeps the "sources" stem, so
# anyone scanning the directory reads it as a source file and it sorts
# immediately beside the project's own; the suffix names the tool that wrote
# it, so a reader can tell at a glance which of the two is theirs. dbt's own
# style guide prefixes source files with an underscore (_project__sources.yml),
# which sorts them to the top of the directory -- away from the file this one
# exists to sit next to.
_ALT_SOURCES_NAME = "sources_dbtw.yml"

# How many of the project's own files the in-project refusal names. Enough to
# make "your files are in there" concrete; not the whole tree.
_NAMED_AT_RISK = 3


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
    # Files the previous write into this out_dir produced and this one does
    # not, removed by this write. Each also has a Decision in `decisions`;
    # the paths are here so a screen can list them as it lists `paths`.
    removed: tuple[Path, ...] = ()


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


class OrphanSchemaTestError(ValueError):
    """A SchemaTest names a model this change does not carry.

    Unreachable through the pipeline: `assemble` records a test only inside
    the loop over its own models, naming the model it is standing on, and
    settles final names before that loop begins. Raising rather than
    dropping, for the same reason `DuplicateSourceEntryError` raises: the
    model loop simply never asks for an orphan's file, so what silence buys
    is a run that writes no .yml and a report that counts one anyway --
    `Tests: 1` beside an out_dir holding none. Not in the CLI's
    `_USAGE_ERRORS`: no input can produce it, so reaching it is a dbtw bug
    and should surface as one.
    """


class OrphanModelDescriptionError(ValueError):
    """A ModelDescription names a model this change does not carry.

    The same shape as `OrphanSchemaTestError`, and here for the same reason.
    Unreachable through the pipeline -- `assemble._model_descriptions` refuses
    an unknown model before a `ProjectChange` is built -- but the loop below
    asks each model for its description and no model asks for an orphan's, so
    what silence buys here is worse than a miscount: it is a sentence the
    reader wrote, shown to them on a screen, and dropped between that screen
    and the disk. Not in the CLI's `_USAGE_ERRORS`: no input can produce it,
    so reaching it is a dbtw bug and should surface as one.
    """


class OutputInsideProjectError(ValueError):
    """out_dir is the target dbt project itself, or a directory inside it.

    Input-driven: the caller's paths can't work as given, and it is not a dbtw
    bug, so a front end should report it rather than let it surface as a
    traceback. The CLI keeps it in `_USAGE_ERRORS` for that reason and the web
    layer renders it on the screen the write was pressed from.

    Subclasses ValueError for the same reason UnsafeOutputPathError does -- a
    caller catching ValueError around a conversion keeps catching this one.

    A separate type from UnsafeOutputPathError, which answers a different
    question. That one is emit's last line of defense against a *model path*
    escaping out_dir, and it fires while writing. This one is about out_dir
    itself and fires before anything is written at all, because by the time a
    model path could notice, out_dir is the project and the first model file
    has already landed on top of the user's own.
    """


class StaleOutputError(ValueError):
    """The previous write's files cannot be cleared safely.

    Two causes, both input-driven, so a usage error to every front end: the
    record of the previous write cannot be read, or a file that write
    produced, and that this write no longer produces, has been changed since.

    Refused rather than worked around. Reading a damaged record as empty
    would leave the previous write's stale files in place, to be delivered
    beside this one's -- the defect this record exists to remove, with
    nothing said. Removing an edited file would delete the reader's work.
    Raised before anything is written, so the out_dir is as it was.
    """


class UnsafeOutputPathError(ValueError):
    """A model's path would resolve outside out_dir. Input-driven — the model
    name came from the source SQL (e.g. a quoted identifier like
    "../../escape") — never a dbtw bug, so callers should treat it as a
    usage error, not a crash. Subclasses ValueError: emit() already raised
    plain ValueError here, and existing callers/tests that catch ValueError
    must keep working unchanged.
    """


def _existing_chain(path: Path) -> list[Path]:
    """`path` resolved, then it and its ancestors, keeping the ones that exist.

    An out_dir that has not been created yet still has to be placed, and a
    stat-based comparison needs something on disk to stat: the nearest
    ancestor that does exist is the first entry, and everything above it
    exists too. `mkdir -p` would put the new directory inside that ancestor,
    so an ancestor that is the project is an out_dir that is inside the
    project.

    The resolve() is the point of this function rather than a detail of it.
    Path("out").parents is (Path("."),), Path(".").parents is empty, and a
    symlink's parents are the link's own rather than its target's — so walking
    the path as given stops short of the project for an out_dir that is
    relative, that is ".", or that is a symlink into a project subdirectory,
    which is three of the ordinary ways to write "inside the project". It
    costs nothing that matters: resolve() expands symlinks but leaves case
    unfolded, and `_same_dir` is what answers for case anyway.
    """
    resolved = path.resolve()
    return [candidate for candidate in (resolved, *resolved.parents) if candidate.exists()]


def _same_dir(one: Path, other: Path) -> bool:
    """Whether two paths are one directory on disk. Both must exist.

    NOT `one.resolve() == other.resolve()`. resolve() expands symlinks but
    does not canonicalise case, so on a case-insensitive filesystem — APFS and
    NTFS, which is most desktops — /x/PROJ and /x/proj resolve to two
    different strings and are one directory; a string comparison lets an
    out_dir of /x/PROJ convert straight into /x/proj. os.path.samefile
    compares the stat dev/ino pair, which is the identity the filesystem
    itself uses, and answers for case-folding, symlinks, hardlinked
    directories and bind mounts in one test.

    An OSError from stat (a path that vanished between the exists() check and
    here, or one we cannot stat) is left to propagate: OSError is already a
    usage error to every front end, and swallowing it here would turn "cannot
    tell" into "not the project", which is the answer that destroys the
    project.
    """
    return one.samefile(other)


def _refuse(relation: str, ctx: ProjectContext) -> NoReturn:
    at_risk = sorted(
        {s.declared_in for s in ctx.existing_sources} | {m.path for m in ctx.existing_models}
    )
    named = ", ".join(at_risk[:_NAMED_AT_RISK]) if at_risk else "dbt_project.yml"
    raise OutputInsideProjectError(
        f"refusing to convert into the target project. {relation}. "
        "Each file that this conversion writes goes to a project-relative path. "
        "So the conversion would write directly into the project, where "
        f"{named} already exist. The conversion would replace those files in place, "
        "and no copy would stay for comparison. Convert into a directory outside "
        "the project. Then read the report, and copy the files that you want into "
        "the project."
    )


def refuse_output_inside_project(out_dir: Path, ctx: ProjectContext) -> None:
    """Refuse a run that would write into the dbt project it converts against.

    dbtw's whole contract is that it hands you a copy to read before you
    change anything: every file it writes lands at a *project-relative* path,
    so an out_dir that is the project writes the conversion's models and
    sources file straight over the project's own — the declaration this
    conversion doesn't repeat is gone from the real project, immediately, with
    no copy left to compare against and a CONVERSION_REPORT.md sitting in
    there claiming a clean run.

    It lives here and not on a front end. This used to be the CLI's, because
    `ProjectContext` carried no path to the project it was read from and the
    CLI held both arguments; it does carry one now (`ProjectContext.root`),
    and `emit` is the only thing in this package that writes. A guard a caller
    has to remember to call is a guard a caller can forget, and the front end
    aimed at people least able to notice is the one that was about to.

    Public, and called a second time, for the same reason it moved here:
    `dbtw web` reads a project and holds a conversation for as long as the
    user wants before anything is written, and the ordinary case is a user
    standing in their own project with `--out` defaulted to `./dbtw-out` —
    exactly what `dbtw convert` refuses before it reads a single statement.
    Waiting for `emit()` to raise means the refusal arrives when the write
    button is pressed, after a walk built for people with no dbt knowledge
    has already spent six screens on a conversation that was never going
    anywhere. `_web` calls this once, at startup, beside the project it
    already reads for `NotADbtProjectError`; `emit()` still calls it too, so
    a caller that skips the CLI's check is not left unguarded.

    It runs before out_dir is created, not during the write: a refusal raised
    after three model files have landed has already done the damage it exists
    to prevent, and a refusal that has created a directory inside the user's
    project has left something behind for them to find and wonder about.

    Two ways a run reaches the project, and both are asked as questions about
    filesystem identity rather than about path spelling:

    (1) out_dir *is* the project, or sits inside it. Asked of out_dir and
        every existing ancestor, so an out_dir that does not exist yet is
        placed by the directory it would be created in.
    (2) out_dir *holds* the project's model-path by another route. A project
        whose models/ is a symlink into a shared tree is neither the root nor
        under it when out_dir is that shared tree — and yet every model this
        run writes lands in the project's real models directory. Each
        configured model-path is checked as `out_dir/<path>` against
        `root/<path>`, which is exactly the pairing the write below uses.

    out_dir merely *containing* the project is still allowed: the project sits
    inside out_dir there, at a path this run writes nothing to.
    """
    out_resolved = out_dir.resolve()
    project_resolved = ctx.root.resolve()

    for ancestor in _existing_chain(out_dir):
        if _same_dir(ancestor, ctx.root):
            preposition = (
                "is the dbt project at"
                if ancestor == out_resolved
                else "is inside the dbt project at"
            )
            _refuse(
                f"The output directory resolves to {out_resolved}, which {preposition} "
                f"{project_resolved}",
                ctx,
            )

    for relative in ctx.model_paths:
        theirs = ctx.root / relative
        ours = out_dir / relative
        if theirs.is_dir() and ours.exists() and _same_dir(ours, theirs):
            _refuse(
                f"The output directory resolves to {out_resolved}. The directory "
                f"{relative}/ in the output directory is the same directory as {relative}/ "
                f"in the dbt project at {project_resolved}. Two paths go to one directory",
                ctx,
            )


def emit(
    change: ProjectChange,
    ctx: ProjectContext,
    out_dir: Path,
    progress: Progress = NOTHING_EXPLAINED,
) -> EmitResult:
    # First, and before out_dir is created: `ctx` was read from a real dbt
    # project, and writing this conversion into that project is the one thing
    # this package must never do.
    refuse_output_inside_project(out_dir, ctx)

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
    # below, so that `render_schema_yaml` is never handed two models' tests in
    # one call -- the caller bug it refuses loudly.
    #
    # Checked here too, and before anything is written: a test naming a model
    # this change does not carry would be dropped by the loop below without a
    # word, since the loop asks each model for its tests and no model asks for
    # an orphan's. The report counts `change.tests` regardless, so the silence
    # costs a run that says `Tests: 1` over an out_dir with no .yml in it.
    tests_by_model: dict[str, list[SchemaTest]] = {}
    for test in change.tests:
        tests_by_model.setdefault(test.model, []).append(test)
    orphans = sorted(set(tests_by_model) - {model.name for model in change.models})
    if orphans:
        models = "that model" if len(orphans) == 1 else "those models"
        raise OrphanSchemaTestError(
            f"change.tests names {_listed(orphans)}, but change.models does not "
            f"carry {models}. So emit would write no file for the tests of {models}, "
            "and the report would still count those tests. Each test is recorded against "
            "the model that it was chosen for. A test that names no model in the change "
            "is a dbtw bug."
        )

    # Checked before anything is written, for the reason the tests above are
    # -- and with more at stake. A description naming a model this change does
    # not carry would be dropped by the loop below without a word, and a
    # dropped description is the reader's own sentence going missing between
    # the screen that showed it and the file they commit.
    described = {entry.model: entry.text for entry in change.descriptions}
    undescribable = sorted(set(described) - {model.name for model in change.models})
    if undescribable:
        models = "that model" if len(undescribable) == 1 else "those models"
        raise OrphanModelDescriptionError(
            f"change.descriptions names {_listed(undescribable)}, but change.models "
            f"does not carry {models}. So emit would lose the reader's own words. "
            "Emit would write no file for those words and would tell nobody. Each "
            "description is written against a model that the walk showed. A description "
            "that names no model in the change is a dbtw bug."
        )

    # Every file rendered before any is written, so that what the previous
    # write left behind is known -- and refused, if it cannot be cleared --
    # while the out_dir is still exactly as that write left it.
    planned: list[tuple[Path, str]] = []
    for model in change.models:
        planned.append((_safe_join(out_dir, model.path), render_model(model)))

        # A .yml is written for a model that has a test, a description, or
        # both -- one file per model, not one per thing said about it. A
        # description alone is the ordinary case: describing a model is asked
        # of every reader, and answering a question is not.
        model_tests = tuple(tests_by_model.get(model.name, ()))
        schema = render_schema_yaml(model.name, model_tests, described.get(model.name, ""))
        if schema:
            planned.append((_safe_join(out_dir, _schema_yaml_rel(model)), schema))

    if change.sources:
        planned.append((_safe_join(out_dir, sources_rel), render_sources_yaml(change.sources)))

    report_path = _safe_join(out_dir, REPORT_NAME)
    produced = {path.relative_to(out_dir).as_posix() for path, _ in planned} | {REPORT_NAME}
    stale = _stale(out_dir, _read_record(out_dir), produced)
    removals = tuple(_removed(relative) for relative in stale)

    # The report has to answer for every file emit wrote, and for every file
    # it removed. Where the sources file landed and what the previous write
    # left behind are only knowable here.
    emitted = (*placement, *removals)
    reported = (
        dataclasses.replace(change, decisions=(*change.decisions, *emitted)) if emitted else change
    )

    for path, text in planned:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)

    # After this write's own files and before the report: the report says
    # these are gone, so it is written once they are.
    removed = tuple(_safe_join(out_dir, relative) for relative in stale)
    for path in removed:
        path.unlink()

    report_path.write_text(render_report(reported, ctx, progress), encoding="utf-8")
    written.append(report_path)

    # Last, so the record never lists a file this write did not finish. A
    # write that fails part way leaves the previous record in place, and the
    # next write clears against it: every path it names that is still there
    # and unchanged is still ours.
    _write_record(out_dir, written)

    return EmitResult(paths=tuple(written), decisions=emitted, removed=removed)


def written_by_last_emit(out_dir: Path) -> tuple[str, ...]:
    """The files the latest write into `out_dir` produced, out_dir-relative.

    Empty when nothing has been written there by this tool -- no record at
    all. A record that cannot be read raises `StaleOutputError` rather than
    reading as empty, for the reason that class gives.
    """
    return tuple(_read_record(out_dir))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_record(out_dir: Path) -> dict[str, str]:
    """The previous write's files, by out_dir-relative path, with the sha256
    of the bytes it wrote. Empty when there is no record.

    No record is the one ordinary empty case: an out_dir this tool has not
    written to, or one written before the record existed. Everything else
    that is not a record in the shape `_write_record` writes is refused.
    """
    record_path = out_dir / WRITE_RECORD_NAME
    if not record_path.exists():
        return {}
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError both
        raise _unreadable(record_path, f"The record is not JSON ({exc})") from exc
    if not isinstance(record, dict) or record.get("format") != _RECORD_FORMAT:
        raise _unreadable(record_path, f"The record is not a format-{_RECORD_FORMAT} record")
    files = record.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(path, str) and isinstance(digest, str) for path, digest in files.items()
    ):
        raise _unreadable(record_path, "The file list in the record is not a map of path to digest")
    if WRITE_RECORD_NAME in files:
        raise _unreadable(record_path, "The record lists itself as a file of the conversion")
    return dict(files)


def _unreadable(record_path: Path, why: str) -> StaleOutputError:
    return StaleOutputError(
        f"cannot read {record_path}. That file records which files the previous write "
        f"into this directory produced. {why}. Without the record, a file that the "
        "previous write produced and this write does not produce would stay beside the "
        "files of this conversion. Delivery would then copy that file with them. "
        f"Delete {WRITE_RECORD_NAME} and any files of the previous conversion from the "
        "directory. Then write again."
    )


def _stale(out_dir: Path, previous: Mapping[str, str], produced: set[str]) -> tuple[str, ...]:
    """The previous write's files that this write does not produce and that
    are still there, as that write left them. Sorted.

    A recorded path that is no longer there is nothing to remove: the reader
    removed it, which is the state this function exists to reach. A recorded
    path whose bytes have changed is no longer the file this tool wrote, so
    it is refused -- see `StaleOutputError`. Every path is put through
    `_safe_join` first: the record is a file on disk, and a path in it that
    escapes out_dir was never written there by this tool.

    A recorded path can name the same file as a produced path by another
    spelling. On a filesystem that folds case, `Foo.sql` and `foo.sql` are
    one file. Such a path is not stale: this write writes that file again.
    Removing it would remove the new file. The test is file identity
    (`_identity`), not spelling, so it is correct on both kinds of
    filesystem. It runs before the write, while the file is there.
    """
    identities = (_identity(out_dir / relative) for relative in produced)
    taken = {identity for identity in identities if identity is not None}
    stale: list[str] = []
    edited: list[str] = []
    for relative in sorted(set(previous) - produced):
        path = _safe_join(out_dir, relative)
        if not path.exists() and not path.is_symlink():
            continue
        if not path.is_symlink() and _identity(path) in taken:
            continue
        if path.is_symlink() or not path.is_file() or _digest(path) != previous[relative]:
            edited.append(relative)
        else:
            stale.append(relative)
    if edited:
        files = "that file" if len(edited) == 1 else "those files"
        raise StaleOutputError(
            f"The previous write into this directory wrote {_listed(edited)}. "
            f"This conversion no longer produces {files}. But {files} changed after "
            "the previous write. This tool removes only files that are exactly as it "
            "wrote them, so this tool wrote nothing. "
            f"Move {files} out of the directory, or delete {files}. Then write again."
        )
    return tuple(stale)


def _identity(path: Path) -> tuple[int, int] | None:
    """The (device, inode) pair of the file at `path`, or None when no file
    is there. Two paths with one pair are one file, whatever their spelling.
    """
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_dev, stat.st_ino)


def _removed(relative: str) -> Decision:
    """The Decision recording one file this write removed."""
    return _decision(
        "removed",
        relative,
        f"removed {relative}, because the previous write into this directory produced "
        "that file and this write does not",
        "This tool lists each file that it writes into an output directory in "
        f"{WRITE_RECORD_NAME}, beside this report. {relative} was on that list. This "
        "conversion no longer produces that file. The cause is one of two: an answer "
        "moved the model to another layer, or the script no longer builds the model. "
        "If the file stayed, it would sit beside the files of this conversion. A model "
        "that moved to another layer would then be there twice under one name, and dbt "
        "refuses to parse that. A copy of the directory into the project would also copy "
        "the file. This tool never removes a file that it did not write. If this tool "
        "wrote a file and the file changed after that, the write stops instead.",
    )


def _write_record(out_dir: Path, written: list[Path]) -> None:
    """Record which files this write produced, with a digest of each.

    Written beside its final name and moved into place, so a record is
    either the previous write's or this one's, never half of either. The
    digest is of the bytes on disk, read back, rather than of the text
    rendered: it is the file the next write compares against.
    """
    record = {
        "format": _RECORD_FORMAT,
        "files": {path.relative_to(out_dir).as_posix(): _digest(path) for path in written},
    }
    staged = out_dir / f"{WRITE_RECORD_NAME}.tmp"
    staged.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    staged.replace(out_dir / WRITE_RECORD_NAME)


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
            f"refusing to write outside out_dir. The path {relative!r} would resolve to "
            f"{resolved}, which is outside out_dir."
        )
    return target


def _schema_yaml_rel(model: AssembledModel) -> str:
    """The project-relative path a model's schema .yml lands at: `model.path`
    with `.sql` swapped for `.yml`, so the file sits in the same directory as
    the model it tests, under the model's own final name.

    Against the target *project*, this name needs no collision Decision of its
    own. Sources.yml's landing name is a fixed constant ("sources.yml") that
    nothing upstream vouches for -- any project could already have a file at
    that exact path, which is the whole reason `_sources_placement` exists. A
    schema .yml's landing name is different in kind: it is *derived from*
    `model.name`, the model's own final name, which `assemble._final_name`
    already resolves against the target project before this module ever sees
    it. A project file at a NEW model's name is precisely what `assemble`
    already calls a "collision" and records as its own Decision (assembler.py:
    `existing_by_name.get(final_name)`), so restating it here would be a
    second, drift-prone sentence about one choice.

    That reasoning covers the project's files and stops there -- it says
    nothing about the other file *this run* writes into the same directory,
    and that is where the two do meet. `_sources_placement` puts our sources
    file at `<dir>/sources.yml`, so a model named `sources` lands its tests on
    exactly that path; emit writes models before sources, so the sources write
    replaced the test file the user chose, silently, while the report went on
    counting the test. `_sources_placement` treats these paths as taken names
    for that reason, and moves itself rather than the model's file: its own
    name is a constant this module picks, while the .yml's is the model's.
    """
    return Path(model.path).with_suffix(".yml").as_posix()


def _schema_yaml_paths(change: ProjectChange) -> dict[str, str]:
    """Where this run's per-model schema .yml files land, by path, naming the
    model whose chosen tests put each one there.

    Only a model with a test gets a file, so only those names are taken --
    moving the sources file for a model whose .yml is never written would
    rename it around a conflict that is not there.
    """
    tested = {test.model for test in change.tests}
    return {_schema_yaml_rel(model): model.name for model in change.models if model.name in tested}


def _holds(path: str, declared_at: set[str], model_yml_at: Mapping[str, str]) -> str:
    """What already occupies `path`, named in the words the Decision uses."""
    if path in declared_at and path in model_yml_at:
        return (
            "the target project declares sources there, and this conversion writes its "
            f"tests for {model_yml_at[path]} there"
        )
    if path in declared_at:
        return "the target project declares sources there"
    return f"this conversion writes its tests for {model_yml_at[path]} there"


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

    A name this run's own schema .yml files take counts the same way, and for
    a sharper reason: those files land inside out_dir, so the clash is not a
    risk taken on `cp -r` but an overwrite this function would cause by
    itself. See `_schema_yaml_rel`.
    """
    directory = _sources_dir(ctx)
    preferred = (directory / _SOURCES_NAME).as_posix()
    declared_at = {existing.declared_in for existing in ctx.existing_sources}
    model_yml_at = _schema_yaml_paths(change)
    taken = declared_at | set(model_yml_at)
    if preferred not in taken:
        return preferred, ()

    landing = (directory / _ALT_SOURCES_NAME).as_posix()
    bump = 1
    stepped_over: list[str] = []
    while landing in taken:
        stepped_over.append(landing)
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
            "assemble._source_entries skips each (source, table) that the target project "
            "already declares. So a proposed entry can never repeat a declaration of the "
            f"project. But {_listed(overlap)} did repeat one. Emit refuses to write the "
            "placement Decision. That Decision would say that the two files stand "
            "side by side. But the sources file of this conversion would declare a table "
            "that the project already declares."
        )

    named_ours = _listed(sorted(f"{name}.{table}" for name, table in ours))
    # Membership, not truthiness: the question is whether one of this run's
    # schema files lands on `preferred`, and `model` answers that only because
    # an empty model name happens to be unproducible.
    model = model_yml_at.get(preferred, "")
    if at_preferred and preferred in model_yml_at:
        # Two different things hold the ordinary name, for two different
        # reasons, and a Decision naming one of them describes half of what
        # happened. The project's file would be lost on `cp -r`; the tests
        # would be lost here, in out_dir, before anything was copied at all.
        named_theirs = _listed(sorted(f"{name}.{table}" for name, table in theirs))
        held = (
            f"because {preferred} already exists in the target project and holds the "
            f"tests this conversion declares for {model}"
        )
        why = (
            f"{preferred} is taken twice: "
            f"{_holds(preferred, declared_at, model_yml_at)}. The project's file at that "
            f"path declares {named_theirs}. This conversion declares {named_ours} as "
            "sources. This tool writes the sources file after the models. So a sources "
            f"file at that path would overwrite the tests for {model} in this output "
            "directory. When you copy the directory into the project, that sources file "
            "would also replace the project's declarations. So this conversion's sources "
            f"are in a separate file. The tests for {model} keep their file here, and the "
            "project keeps its own file. dbt reads all of these files. The duplicate "
            "check of dbt is per source and table. So two files may declare the same "
            "source name if the tables are different. This conversion skips each table "
            "that the project already declares."
        )
    elif at_preferred:
        named_theirs = _listed(sorted(f"{name}.{table}" for name, table in theirs))
        held = f"because {preferred} already exists in the target project"
        why = (
            f"{preferred} in the target project declares {named_theirs}. This "
            f"conversion declares {named_ours}. A sources file of this conversion at "
            "that path would replace the project's file, and the project's declarations "
            "would go with it. So this conversion's sources are in a separate file, and "
            "this tool changes no file of the project. dbt reads both files. The "
            "duplicate check of dbt is per source and table. So two files may declare "
            "the same source name if the tables are different. This conversion skips "
            "each table that the project already declares. Copy both files into the "
            "project as they are. If you prefer one file, merge this conversion's "
            f"entries into {preferred} by hand."
        )
    else:
        held = f"because {preferred} holds the tests this conversion declares for {model}"
        why = (
            "A model's schema file takes the model's own name. So this conversion "
            f"writes the tests that it was asked to declare for {model} at {preferred}. "
            f"This conversion declares {named_ours} as sources. This tool writes the "
            "sources file after the models. So a sources file at that path would "
            f"replace the tests declared for {model} with source declarations. Then the "
            "run would report a test whose file the same run overwrote. So this "
            "conversion's sources are in a separate file. When you copy the directory, "
            "the two files go into the project together. dbt reads both files."
        )
    # One sentence for each alternative name, so the text stays short for any
    # number of names that were taken.
    for path in stepped_over:
        why += (
            f" The alternative name {path} was also taken: "
            f"{_holds(path, declared_at, model_yml_at)}."
        )
    return landing, (
        _decision(
            "sources_placed",
            landing,
            f"wrote this conversion's sources as {landing}, not {preferred}, {held}",
            why,
        ),
    )
