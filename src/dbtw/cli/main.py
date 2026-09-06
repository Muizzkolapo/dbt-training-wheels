"""The dbtw command-line entry point.

`dbtw convert` runs the whole pipeline — ingest, classify, tier-1 passes,
target-project context, assemble, emit — against a real SQL file (or
directory of them) and a real dbt project, and writes the result to an
output directory.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from dbtw.core.assemble import assemble
from dbtw.core.context import NotADbtProjectError, ProjectContext, read_project
from dbtw.core.emit import UnsafeOutputPathError, emit
from dbtw.core.ingest import UnknownDialectError, classify_statements, ingest
from dbtw.core.passes import run_passes

_REPORT_NAME = "CONVERSION_REPORT.md"


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
_USAGE_ERRORS = (
    UnknownDialectError,
    OSError,
    NotADbtProjectError,
    UnsafeOutputPathError,
    OutputInsideProjectError,
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

    return parser


def _refuse_output_inside_project(
    out_dir: Path, project_root: Path, given_out: str, ctx: ProjectContext
) -> None:
    """Refuse --out when it is the target project, or a directory inside it.

    dbtw's whole contract is that it hands you a copy to read before you
    change anything: every file it writes lands at a *project-relative* path,
    so an out_dir that is the project writes the conversion's models and
    sources.yml straight over the project's own — the source declaration this
    conversion doesn't repeat is gone from the real project, immediately, with
    no copy left to compare against and a CONVERSION_REPORT.md sitting in
    there claiming a clean run.

    Only the CLI can make this call. emit() is handed a ProjectContext, which
    carries no path to the project it was read from, so it cannot tell an
    out_dir that is the project from any other out_dir; the CLI holds both
    arguments. It has to happen before the pipeline writes, not during, since
    a refusal raised after three model files have landed has already done the
    damage it exists to prevent.

    Both paths are expanded and resolved before comparing, so a relative
    --out, a trailing slash, a symlink, '.' and a quoted '~' all reach the
    same answer — the same reasoning `emit._safe_join` uses for its own guard,
    and the same containment test: equal, or the project among out_dir's
    resolved parents.

    out_dir *containing* the project is not refused. The project sits inside
    out_dir there, and nothing this run writes reaches into it.
    """
    out_resolved = out_dir.resolve()
    project_resolved = project_root.resolve()
    if out_resolved != project_resolved:
        if project_resolved not in out_resolved.parents:
            return
        relation = "inside the dbt project at"
    else:
        relation = "the dbt project at"

    at_risk = sorted(
        {s.declared_in for s in ctx.existing_sources} | {m.path for m in ctx.existing_models}
    )
    named = ", ".join(at_risk[:_NAMED_AT_RISK]) if at_risk else "dbt_project.yml"

    raise OutputInsideProjectError(
        f"refusing to convert into the target project: --out {given_out!r} resolves to "
        f"{out_resolved}, which is {relation} {project_resolved}. Every file this "
        "conversion writes lands at a project-relative path, so it would write straight "
        f"into the project — where {named} already live — and replace them in place, "
        "leaving nothing to compare against. Convert into a directory outside the "
        "project, read the report, and copy across what you want."
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
    project_root = Path(project).expanduser()
    out_dir = Path(out).expanduser()

    ingest_result = ingest(sql_path, dialect)
    for warning in ingest_result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    classified = classify_statements(ingest_result)
    state = run_passes(classified, ingest_result.dialect)
    ctx = read_project(project_root)
    _refuse_output_inside_project(out_dir, project_root, out, ctx)
    change = assemble(state, ctx, inline_vars=inline_vars, unique_key=unique_key)

    emit(change, ctx, out_dir)
    report_path = out_dir / _REPORT_NAME

    print(
        f"Read {len(ingest_result.statements)} statements from {sql_path}. "
        f"Wrote {len(change.models)} models, proposed {len(change.sources)} sources; "
        f"{len(change.pending)} statements still pending. Report: {report_path}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    unique_key = (
        tuple(c.strip() for c in args.unique_key.split(",") if c.strip()) if args.unique_key else ()
    )

    try:
        return _convert(
            args.sql_path, args.project, args.out, args.dialect, args.inline_vars, unique_key
        )
    except _USAGE_ERRORS as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
