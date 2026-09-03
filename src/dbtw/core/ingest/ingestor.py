"""Reads SQL files and produces RawStatements via the splitter."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sqlglot.dialects.dialect import Dialect

from dbtw.core.ingest.splitter import split_sql
from dbtw.core.ingest.types import IngestResult, RawStatement, UnknownDialectError

_NO_DIALECT_WARNING = "no dialect specified; statements parsed with sqlglot's permissive default"


def ingest(source: Path | str | Sequence[Path | str], dialect: str | None = None) -> IngestResult:
    if dialect is not None and dialect not in Dialect.classes:
        valid = ", ".join(sorted(k for k in Dialect.classes if k))
        raise UnknownDialectError(f"unknown dialect {dialect!r}; valid: {valid}")

    files = _resolve_files(source)
    warnings: list[str] = [] if dialect is not None else [_NO_DIALECT_WARNING]
    statements: list[RawStatement] = []
    for file in files:
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"skipped {file}: {exc}")
            continue
        for index, span in enumerate(split_sql(text, dialect)):
            statements.append(
                RawStatement(
                    source_file=str(file),
                    index=index,
                    text=span.text,
                    line_start=span.line_start,
                    line_end=span.line_end,
                )
            )
    return IngestResult(statements=tuple(statements), dialect=dialect, warnings=tuple(warnings))


def _resolve_files(source: Path | str | Sequence[Path | str]) -> list[Path]:
    if isinstance(source, (str, Path)):
        sources: Sequence[Path | str] = [source]
    else:
        sources = source
    files: list[Path] = []
    for item in sources:
        path = Path(item)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*.sql") if p.is_file()))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"no such file or directory: {path}")
    return files
