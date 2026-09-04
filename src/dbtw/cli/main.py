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
from dbtw.core.context import NotADbtProjectError, read_project
from dbtw.core.emit import UnsafeOutputPathError, emit
from dbtw.core.ingest import UnknownDialectError, classify_statements, ingest
from dbtw.core.passes import run_passes

_REPORT_NAME = "CONVERSION_REPORT.md"

# The input/usage errors that mean "the user's command can't work as given",
# as opposed to a bug in dbtw itself. Reported on stderr with no traceback.
# OSError covers FileNotFoundError plus its siblings that a bad --out can
# raise (FileExistsError when --out names an existing file, PermissionError,
# IsADirectoryError, ...) — all input-driven, not a dbtw bug.
_USAGE_ERRORS = (UnknownDialectError, OSError, NotADbtProjectError, UnsafeOutputPathError)


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

    return parser


def _convert(sql_path: str, project: str, out: str, dialect: str | None) -> int:
    ingest_result = ingest(sql_path, dialect)
    for warning in ingest_result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    classified = classify_statements(ingest_result)
    state = run_passes(classified, ingest_result.dialect)
    ctx = read_project(project)
    change = assemble(state, ctx)

    out_dir = Path(out)
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

    try:
        return _convert(args.sql_path, args.project, args.out, args.dialect)
    except _USAGE_ERRORS as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
