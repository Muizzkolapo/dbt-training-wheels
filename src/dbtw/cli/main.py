"""The dbtw command-line entry point.

`dbtw convert` runs the whole pipeline — ingest, classify, tier-1 passes,
target-project context, assemble, emit — against a real SQL file (or
directory of them) and a real dbt project, and writes the result to an
output directory.

`dbtw web` opens the same conversion as a conversation: one `Session` over
one SQL path and one dbt project, answered a question at a time, served on
the loopback address until the user stops it. What this command owns is the
two refusals that belong on the command line rather than on a screen — a
project that is not a dbt project (spec section 7), and an install without
the web extra — the session they guard, and where the walk is served.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from dbtw.core.assemble import assemble
from dbtw.core.context import NotADbtProjectError, read_project
from dbtw.core.emit import (
    OutputInsideProjectError,
    UnsafeOutputPathError,
    emit,
    refuse_output_inside_project,
)
from dbtw.core.ingest import UnknownDialectError, classify_statements, ingest
from dbtw.core.passes import run_passes
from dbtw.web import MissingWebExtraError, Session, Source, require_flask

_REPORT_NAME = "CONVERSION_REPORT.md"

# Where `dbtw web` serves. The loopback address and nothing else: a
# conversation holds the contents of the user's SQL and their dbt project,
# and a local tool that put those on the network by default would be making
# that choice on their behalf. There is no flag for it, because "serve this
# to the network" is a decision that deserves more than a flag.
_HOST = "127.0.0.1"

# Flask's own default, so the address is the one a reader expects. A port
# already in use raises OSError from the bind, which is already a usage
# error here — the refusal names the port and --port is the answer to it.
_DEFAULT_PORT = 5000


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
    web.add_argument(
        "sql_path",
        metavar="SQL_PATH",
        nargs="?",
        default=None,
        help="A .sql file or directory of .sql. Omit it to paste or upload in the browser",
    )
    web.add_argument(
        "--project",
        metavar="PROJECT_PATH",
        default=None,
        help="The target dbt project root (or give it in the browser)",
    )
    web.add_argument("--dialect", metavar="DIALECT", default=None, help="The source SQL dialect")
    web.add_argument(
        "--out",
        metavar="OUT_DIR",
        default="./dbtw-out",
        help="Where the walk's write action writes (default: ./dbtw-out)",
    )
    web.add_argument(
        "--port",
        metavar="PORT",
        type=int,
        default=_DEFAULT_PORT,
        help=f"The port to serve the walk on (default: {_DEFAULT_PORT})",
    )
    web.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
        default=True,
        help="Do not open a browser (the address is printed either way)",
    )

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


def _convert(
    sql_path: str,
    project: str,
    out: str,
    dialect: str | None,
    inline_vars: bool,
    unique_key: tuple[str, ...],
) -> int:
    # Expanded here, once, for both arguments: --out and --project have to be
    # read in the same spelling or emit's in-project guard compares '~/proj'
    # against an already-expanded path and lets the very case it exists for
    # through. `read_project` keeps what it is handed as `ProjectContext.root`,
    # so the spelling this function chooses is the one that guard asks about.
    project_root = _expanded(project, "--project")
    out_dir = _expanded(out, "--out")

    ingest_result = ingest(sql_path, dialect)
    for warning in ingest_result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    classified = classify_statements(ingest_result)
    state = run_passes(classified, ingest_result.dialect)
    ctx = read_project(project_root)
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


def _web(
    sql_path: str | None, project: str, out: str, dialect: str | None, port: int, open_browser: bool
) -> int:
    # Asked first, and before anything is read: no argument the user could
    # have written makes this command work without the extra, so a refusal
    # about their --project would send them to fix the wrong thing.
    require_flask()

    # --out is held rather than used here for most of this function: the walk
    # writes when the reader presses the write action and not before, and
    # `test_web_writes_nothing` is what says so. It is read once below, to
    # ask the same question `dbtw convert` asks before it reads a statement.
    #
    # SQL_PATH and --project are both optional, and their absence is a state
    # rather than an error: a reader who has not got a file to point at
    # brings their query in through the entry screen, and gives the project
    # it converts against on the same screen. What this function will not do
    # is invent either for them -- `Source` holds neither until something
    # arrives.
    project_root = _expanded(project, "--project") if project is not None else None
    sql = _expanded(sql_path, "SQL_PATH") if sql_path is not None else None
    out_dir = _expanded(out, "--out")

    if project_root is None and sql is not None:
        # A SQL_PATH with no project cannot open a conversation: a `Session`
        # is a conversation about one script AND one project, and every
        # proposal it makes is read out of that project's dbt_project.yml.
        # Refused rather than carried to the entry screen, because carrying
        # it means this command accepted an argument and then showed a screen
        # asking for it again -- and refused loudly rather than ignored,
        # which is what the first shape of this did.
        raise UnexpandablePathError(
            f"{sql_path} was given with no --project, and a conversion is a proposal about "
            "a dbt project: which layer a model belongs in, what its name is prefixed with, "
            "what a folder already materializes as. Pass --project, or omit SQL_PATH too and "
            "bring both in the browser"
        )

    source = Source(out=out_dir, dialect=dialect)
    if project_root is None:
        # Nothing to read and nothing to check: the project arrives on the
        # entry screen, where `Source.start` reads it and renders the same
        # refusal this would have printed. Saying anything about conventions
        # here would be saying it about a project nobody has named.
        print("Bring your SQL and your dbt project in the browser.")
    else:
        # Spec section 7: a --project that is not a dbt project is a
        # command-line error, not a screen. Read here rather than left to the
        # session's own first run so the refusal is about the project, not
        # about whatever the SQL turns out to do. The session reads it again
        # on every run, which is what keeps a screen current with a project
        # being edited beside it.
        ctx = read_project(project_root)

        # Asked here, not left to `emit()` when the write button is pressed:
        # --out defaults to ./dbtw-out, same as `convert`, so a user standing
        # in their own project is the ordinary case dbtw convert refuses
        # before reading a single statement. Waiting means a person who has
        # never used dbt spends six screens on a conversation that was never
        # going anywhere. `emit()` still asks this question too, so a caller
        # that reaches it any other way is not left unguarded.
        #
        # Only when the project came from the command line. A project given
        # in the browser is checked against --out by `emit` at the moment the
        # write is pressed, which is the first moment the pair exists.
        refuse_output_inside_project(out_dir, ctx)
        source.project = project_root
        if sql is not None:
            source.session = Session(project=project_root, sql=sql, dialect=dialect)
            questions = source.session.questions()
            noun = "question" if len(questions) == 1 else "questions"
            print(f"{sql} → {ctx.project_name}: {len(questions)} {noun} to answer.")
        else:
            # No count, because there is nothing yet to count. Saying "0
            # questions" here would report a conversion this command has not
            # been given anything to make.
            print(f"{ctx.project_name}: bring your SQL in the browser.")

    # Imported here, after require_flask(), so that `dbtw convert` — and
    # `dbtw.web` itself — go on importing without the extra. `dbtw.web.app`
    # is the one module in the package that imports Flask at its top, and
    # this is the one line that reaches it.
    from dbtw.web.app import create_app, serve

    serve(create_app(source), _HOST, port, open_browser)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "web":
            return _web(
                args.sql_path,
                args.project,
                args.out,
                args.dialect,
                args.port,
                args.open_browser,
            )

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
