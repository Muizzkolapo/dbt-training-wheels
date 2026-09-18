"""The dbtw command-line entry point.

`dbtw convert` runs the whole pipeline — ingest, classify, tier-1 passes,
target-project context, assemble, emit — against a real SQL file (or
directory of them) and a real dbt project, and writes the result to an
output directory.

`dbtw web` opens the same conversion as a conversation: one `Session` over
one SQL path and one dbt project, answered a question at a time. What this
command owns is the two refusals that belong on the command line rather than
on a screen — a project that is not a dbt project (spec section 7), and an
install without the web extra — and the session they guard.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from dbtw.core.assemble import assemble
from dbtw.core.context import NotADbtProjectError, ProjectContext, read_project
from dbtw.core.emit import UnsafeOutputPathError, emit
from dbtw.core.ingest import UnknownDialectError, classify_statements, ingest
from dbtw.core.passes import run_passes
from dbtw.web import MissingWebExtraError, Session, require_flask

_REPORT_NAME = "CONVERSION_REPORT.md"


class UnexpandablePathError(ValueError):
    """A path argument starts with a '~' that cannot be expanded.

    Path.expanduser() raises RuntimeError for an unknown user
    ('~nosuchuser/out') and for a bare '~' when no home directory can be
    determined at all, which is routine inside a container. RuntimeError is
    not in _USAGE_ERRORS and never should be — it is the shape of a dbtw bug
    — so it escapes main() as a traceback, contradicting the convention
    recorded on _USAGE_ERRORS itself. Caught at the expansion site and
    re-raised as what it actually is: a path the user gave that cannot be
    read.
    """


class OutputInsideProjectError(ValueError):
    """--out names the target dbt project itself, or a directory inside it.

    Input-driven, like every other member of _USAGE_ERRORS: the user's command
    can't work as given, and it is not a dbtw bug. Subclasses ValueError for
    the same reason UnsafeOutputPathError does — a caller catching ValueError
    around a conversion keeps catching this one.

    A separate type from UnsafeOutputPathError, which answers a different
    question. That one is emit's last line of defense against a *model path*
    escaping out_dir, and it fires while writing. This one is about out_dir
    itself and fires before the pipeline writes anything at all, because by
    the time emit could notice, out_dir is the project and the first model
    file has already landed on top of the user's own.
    """


# The input/usage errors that mean "the user's command can't work as given",
# as opposed to a bug in dbtw itself. Reported on stderr with no traceback.
# OSError covers FileNotFoundError plus its siblings that a bad --out can
# raise (FileExistsError when --out names an existing file, PermissionError,
# IsADirectoryError, ...) — all input-driven, not a dbtw bug.
# MissingWebExtraError is here on the same test and not because it is an
# input: an install without the web extra is something the user can change,
# and the message says what to change it to. Contrast DuplicateSourceEntryError
# and MulticolumnCheckedAnswerError, deliberately absent because no input can
# produce them, so reaching one is a dbtw bug and must surface as a traceback.
_USAGE_ERRORS = (
    UnknownDialectError,
    OSError,
    NotADbtProjectError,
    UnsafeOutputPathError,
    OutputInsideProjectError,
    UnexpandablePathError,
    MissingWebExtraError,
)

# How many of the project's own files the refusal names. Enough to make
# "your files are in there" concrete; not the whole tree.
_NAMED_AT_RISK = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dbtw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser(
        "convert", help="Convert raw SQL into models for a target dbt project"
    )
    convert.add_argument("sql_path", metavar="SQL_PATH", help="A .sql file or directory of .sql")
    convert.add_argument(
        "--project", metavar="PROJECT_PATH", required=True, help="The target dbt project root"
    )
    convert.add_argument(
        "--out",
        metavar="OUT_DIR",
        default="./dbtw-out",
        help="Output directory (default: ./dbtw-out)",
    )
    convert.add_argument(
        "--dialect", metavar="DIALECT", default=None, help="The source SQL dialect"
    )
    convert.add_argument(
        "--inline-vars",
        action="store_true",
        default=False,
        help="replace script variables with their literal values instead of dbt var() calls",
    )
    convert.add_argument(
        "--unique-key",
        metavar="COLUMNS",
        default=None,
        help=(
            "comma-separated column(s) that answer the append-or-merge question for "
            "models converted from a bare INSERT...SELECT: upgrades every append "
            "incremental to a merge incremental keyed on these columns"
        ),
    )

    web = subparsers.add_parser(
        "web", help="Answer this conversion's questions one at a time, in a browser"
    )
    web.add_argument("sql_path", metavar="SQL_PATH", help="A .sql file or directory of .sql")
    web.add_argument(
        "--project", metavar="PROJECT_PATH", required=True, help="The target dbt project root"
    )
    web.add_argument("--dialect", metavar="DIALECT", default=None, help="The source SQL dialect")

    return parser


def _expanded(value: str, argument: str) -> Path:
    """One path argument, with a leading '~' expanded, or a usage error.

    Both arguments go through this, not just --out: expanding one side and
    not the other compares '~/proj' against an already-expanded path, and the
    in-project guard misses the very case it exists for.
    """
    try:
        return Path(value).expanduser()
    except RuntimeError as exc:
        raise UnexpandablePathError(f"{argument} {value!r} could not be expanded: {exc}") from exc


def _existing_chain(path: Path) -> list[Path]:
    """`path` resolved, then it and its ancestors, keeping the ones that exist.

    An --out that has not been created yet still has to be placed, and a
    stat-based comparison needs something on disk to stat: the nearest
    ancestor that does exist is the first entry, and everything above it
    exists too. `mkdir -p` would put the new directory inside that ancestor,
    so an ancestor that is the project is an --out that is inside the project.

    The resolve() is the point of this function rather than a detail of it.
    Path("out").parents is (Path("."),), Path(".").parents is empty, and a
    symlink's parents are the link's own rather than its target's — so walking
    the path as given stops short of the project for an --out that is
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
    different strings and are one directory; a string comparison lets
    `--out /x/PROJ` convert straight into `/x/proj`. os.path.samefile compares
    the stat dev/ino pair, which is the identity the filesystem itself uses,
    and answers for case-folding, symlinks, hardlinked directories and bind
    mounts in one test.

    An OSError from stat (a path that vanished between the exists() check and
    here, or one we cannot stat) is left to propagate: OSError is already a
    usage error to main(), and swallowing it here would turn "cannot tell"
    into "not the project", which is the answer that destroys the project.
    """
    return one.samefile(other)


def _refuse(given_out: str, relation: str, ctx: ProjectContext) -> NoReturn:
    at_risk = sorted(
        {s.declared_in for s in ctx.existing_sources} | {m.path for m in ctx.existing_models}
    )
    named = ", ".join(at_risk[:_NAMED_AT_RISK]) if at_risk else "dbt_project.yml"
    raise OutputInsideProjectError(
        f"refusing to convert into the target project: --out {given_out!r} {relation}. "
        "Every file this conversion writes lands at a project-relative path, so it would "
        f"write straight into the project — where {named} already live — and replace them "
        "in place, leaving nothing to compare against. Convert into a directory outside "
        "the project, read the report, and copy across what you want."
    )


def _refuse_output_inside_project(
    out_dir: Path, project_root: Path, given_out: str, ctx: ProjectContext
) -> None:
    """Refuse --out when this run would write into the target project.

    dbtw's whole contract is that it hands you a copy to read before you
    change anything: every file it writes lands at a *project-relative* path,
    so an out_dir that is the project writes the conversion's models and
    sources file straight over the project's own — the declaration this
    conversion doesn't repeat is gone from the real project, immediately, with
    no copy left to compare against and a CONVERSION_REPORT.md sitting in
    there claiming a clean run.

    Only the CLI can make this call. emit() is handed a ProjectContext, which
    carries no path to the project it was read from, so it cannot tell an
    out_dir that is the project from any other out_dir; the CLI holds both
    arguments. It has to happen before the pipeline writes, not during, since
    a refusal raised after three model files have landed has already done the
    damage it exists to prevent.

    Two ways this run reaches the project, and both are asked as questions
    about filesystem identity rather than about path spelling:

    (1) out_dir *is* the project, or sits inside it. Asked of out_dir and
        every existing ancestor, so an --out that does not exist yet is placed
        by the directory it would be created in.
    (2) out_dir *holds* the project's model-path by another route. A project
        whose models/ is a symlink into a shared tree is neither the root nor
        under it when out_dir is that shared tree — and yet every model this
        run writes lands in the project's real models directory. Each
        configured model-path is checked as `out_dir/<path>` against
        `project_root/<path>`, which is exactly the pairing emit will use.

    out_dir merely *containing* the project is still allowed: the project sits
    inside out_dir there, at a path this run writes nothing to.
    """
    out_resolved = out_dir.resolve()
    project_resolved = project_root.resolve()

    for ancestor in _existing_chain(out_dir):
        if _same_dir(ancestor, project_root):
            preposition = (
                "is the dbt project at"
                if ancestor == out_resolved
                else "is inside the dbt project at"
            )
            _refuse(
                given_out,
                f"resolves to {out_resolved}, which {preposition} {project_resolved}",
                ctx,
            )

    for relative in ctx.model_paths:
        theirs = project_root / relative
        ours = out_dir / relative
        if theirs.is_dir() and ours.exists() and _same_dir(ours, theirs):
            _refuse(
                given_out,
                f"resolves to {out_resolved}, whose {relative}/ is the same directory as "
                f"{relative}/ in the dbt project at {project_resolved} — one directory "
                "reached by two paths",
                ctx,
            )


def _convert(
    sql_path: str,
    project: str,
    out: str,
    dialect: str | None,
    inline_vars: bool,
    unique_key: tuple[str, ...],
) -> int:
    # Expanded here, once, for both arguments: --out and --project have to be
    # read in the same spelling or the guard below compares '~/proj' against
    # an already-expanded path and lets the very case it exists for through.
    project_root = _expanded(project, "--project")
    out_dir = _expanded(out, "--out")

    ingest_result = ingest(sql_path, dialect)
    for warning in ingest_result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    classified = classify_statements(ingest_result)
    state = run_passes(classified, ingest_result.dialect)
    ctx = read_project(project_root)
    _refuse_output_inside_project(out_dir, project_root, out, ctx)
    change = assemble(state, ctx, inline_vars=inline_vars, unique_key=unique_key)

    result = emit(change, ctx, out_dir)
    report_path = out_dir / _REPORT_NAME

    # emit's own Decisions, in emit's own words. A user who reads the terminal
    # and then runs `cp -r` never opens the report, and where the sources file
    # landed is the one thing they need before they do.
    for decision in result.decisions:
        print(f"note: {decision.action}", file=sys.stderr)

    print(
        f"Read {len(ingest_result.statements)} statements from {sql_path}. "
        f"Wrote {len(change.models)} models, proposed {len(change.sources)} sources; "
        f"{len(change.pending)} statements still pending. Report: {report_path}"
    )
    return 0


def _web(sql_path: str, project: str, dialect: str | None) -> int:
    # Asked first, and before anything is read: no argument the user could
    # have written makes this command work without the extra, so a refusal
    # about their --project would send them to fix the wrong thing.
    require_flask()

    # Both arguments expanded, for the reason `_expanded` gives.
    project_root = _expanded(project, "--project")
    sql = _expanded(sql_path, "SQL_PATH")

    # Spec section 7: a missing dbt_project.yml is a command-line error, not a
    # screen. Read here rather than left to the session's own first run so the
    # refusal is about the project, not about whatever the SQL turns out to
    # do. The session reads it again on every run, which is what keeps a
    # screen current with a project being edited beside it.
    ctx = read_project(project_root)

    session = Session(project=project_root, sql=sql, dialect=dialect)
    questions = session.questions()
    noun = "question" if len(questions) == 1 else "questions"
    print(f"{sql} → {ctx.project_name}: {len(questions)} {noun} to answer.")
    print(
        "The screens that ask them are not served by this build; nothing was written.",
        file=sys.stderr,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "web":
            return _web(args.sql_path, args.project, args.dialect)

        unique_key = (
            tuple(c.strip() for c in args.unique_key.split(",") if c.strip())
            if args.unique_key
            else ()
        )
        return _convert(
            args.sql_path, args.project, args.out, args.dialect, args.inline_vars, unique_key
        )
    except _USAGE_ERRORS as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
