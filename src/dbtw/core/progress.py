"""What this project's earlier conversions have already explained to a reader.

Every other record in this engine is a fact about the reader's SQL. This one
is a fact about the *reader*, and it exists because the tool was stateless: its
tenth conversion of a project said the same words in the same place as its
first, with the same definitions beside them. The design's own walkthroughs
found four of five readers never reached a glossary block; repeating one
verbatim ten times is how the fifth stops reading it too.

So a word is explained in full the first `FULL_SHOWINGS` times a reader meets
it, and after that it is moved out of the way -- named, with its meaning still
in the same report, below the material that is new. Moved, never dropped: a
word met three runs ago is still a word they may have forgotten, and the
meaning has to stay reachable in the report in front of them rather than in
one they no longer have.

Two rules hold the whole thing honest:

- A word counts when it was actually put in front of the reader, which is
  what `terms_in` already decides for a screen or a report. Counting the
  glossary would retire words nobody has seen.
- A record that cannot be read is never treated as an empty one. An empty
  record means "explain everything", which is exactly what a damaged record
  would silently cause -- so `load` reports the damage instead, `save`
  refuses to write over it, and the reader is told.

The record lives beside the reader rather than beside the project: it is one
plain JSON file they can read and delete, keyed by project, and it is not in
the conversion output (which is thrown away) or in the dbt project (which is
shared with a team who have met different words).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

try:  # POSIX only; the record is locked where the platform can lock it
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

from dbtw.core.teach import Term

# How many times a word is explained in full before it is moved out of the
# way. Three rather than one: a reader who met `ref()` once in a conversion
# they skimmed has not met it. Three rather than ten: the point is that the
# tenth conversion does not look like the first.
FULL_SHOWINGS = 3

_FILE_VERSION = 1


@dataclass(frozen=True, slots=True)
class Progress:
    """One project's record. `shown` is read-only; build a new one to change it.

    `unreadable` is empty for every record that was read, including a record
    that does not exist yet -- a first conversion is not a failure. It carries
    the reason and the path when a record was found and could not be used, so
    a renderer can say so rather than let a silent reset look like a fresh
    start.
    """

    conversions: int = 0
    shown: Mapping[str, int] = field(default_factory=dict)
    unreadable: str = ""


# The record of a reader who has converted this project before and been shown
# nothing -- which is every reader's first conversion, and the honest default
# for any caller that has not looked one up. A singleton because it is a
# default argument, and a named one because `Progress()` at a call site reads
# like an empty value rather than like a claim.
NOTHING_EXPLAINED = Progress()


@dataclass(frozen=True, slots=True)
class Teaching:
    """How much to say about each word on one screen or in one report.

    `full` keeps the order the reader meets the words in, because that is the
    order `terms_in` returns and the order the surrounding prose uses them.
    """

    full: tuple[Term, ...] = ()
    met: tuple[Term, ...] = ()


# A screen with nothing to define -- a refusal page, a 404. Named for the same
# reason NOTHING_EXPLAINED is: `Teaching()` at a call site reads like a value
# that was not filled in rather than like "this screen defines no words".
NO_GLOSSARY = Teaching()


def teaching_for(terms: Sequence[Term], progress: Progress) -> Teaching:
    """Split the words in play into the ones to explain and the ones to name."""
    full: list[Term] = []
    met: list[Term] = []
    for term in terms:
        (met if progress.shown.get(term.name, 0) >= FULL_SHOWINGS else full).append(term)
    return Teaching(full=tuple(full), met=tuple(met))


def after(terms: Sequence[Term], progress: Progress) -> Progress:
    """`progress` once a conversion showing `terms` has been recorded.

    Counts stop at `FULL_SHOWINGS`. A folded word was named, not explained,
    so a count that went on rising would claim showings that never happened
    -- and the number is the only evidence this module reasons from.
    """
    shown = dict(progress.shown)
    for term in terms:
        shown[term.name] = min(shown.get(term.name, 0) + 1, FULL_SHOWINGS)
    return Progress(conversions=progress.conversions + 1, shown=shown)


def state_path() -> Path:
    """Where the record lives when a caller names no other place.

    `XDG_STATE_HOME` when it is set, else `~/.local/state` -- the directory
    for data that is neither configuration the reader wrote nor a cache that
    can be regenerated, which is exactly what this is.
    """
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "dbtw" / "learned.json"


def _key(project: Path) -> str:
    return str(Path(project).expanduser().resolve())


def _stored_key(projects: Mapping[str, object], project: Path) -> str:
    """The key this project is already recorded under, or the one to use.

    `Path.resolve()` normalises symlinks, `..` and a trailing slash, but it
    keeps whatever letter-casing was typed -- and on a case-insensitive
    filesystem (macOS, Windows) `~/Work/Analytics` and `~/work/analytics` are
    one directory. Keyed by the spelling alone, the same project got two
    records and a reader's curriculum silently restarted the first time they
    typed it differently.

    Identity is which directory it *is*, so an exact miss falls back to asking
    the filesystem. A stored key that no longer exists cannot be compared and
    is simply not a match -- it is someone's old project, not this one.
    """
    key = _key(project)
    if key in projects:
        return key
    for stored in projects:
        try:
            if os.path.samefile(stored, project):
                return stored
        except OSError:
            continue
    return key


def _fault(path: Path, detail: str) -> str:
    return f"the record of what you have been shown ({path}) could not be read: {detail}"


def _counts_fault(shown: object) -> str:
    """Why `shown` is not a mapping of word to how often it was explained."""
    if not isinstance(shown, dict):
        return f"a project's 'shown' holds {type(shown).__name__}, not an object"
    for name, count in shown.items():
        if not isinstance(name, str):
            return f"a word is recorded under {type(name).__name__}, not a name"
        # `bool` is an `int` in Python, so True would pass a bare isinstance
        # check and count as one showing -- a record this tool never wrote.
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return f"{name!r} was explained {count!r} times, which is not a count"
    return ""


def _document_fault(loaded: object, path: Path) -> str:
    """Why `loaded` is not a record this tool wrote, or "" when it is.

    Checked whole rather than only the entry being asked for, because both
    readers of this file depend on the answer: `load_progress` must not report
    "nothing explained" for a file it did not understand -- that is the one
    reading indistinguishable from a first conversion -- and `save_progress`
    must not write over a file it did not understand.
    """
    if not isinstance(loaded, dict):
        return _fault(path, f"it holds {type(loaded).__name__}, not an object")
    version = loaded.get("version")
    if version is not None:
        if isinstance(version, bool) or not isinstance(version, int):
            return _fault(path, f"its version is {version!r}")
        if version > _FILE_VERSION:
            # Refused rather than half-read. A later version writing a field
            # this one does not understand, read for the parts it recognises
            # and then saved back, is how a whole curriculum disappears.
            return _fault(
                path,
                f"it was written by a newer version of this tool (format {version}, "
                f"this one understands {_FILE_VERSION})",
            )
    projects = loaded.get("projects")
    if projects is None:
        return ""
    if not isinstance(projects, dict):
        return _fault(path, f"its 'projects' holds {type(projects).__name__}, not an object")
    for name, entry in projects.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            return _fault(path, "a project is recorded in a shape this tool does not write")
        conversions = entry.get("conversions")
        if conversions is not None and (
            isinstance(conversions, bool) or not isinstance(conversions, int) or conversions < 0
        ):
            return _fault(path, f"a project records {conversions!r} conversions")
        shown = entry.get("shown")
        if shown is not None:
            fault = _counts_fault(shown)
            if fault:
                return _fault(path, fault)
    return ""


def _read_file(path: Path) -> tuple[dict[str, object], str]:
    """The whole record, and why it could not be read when it could not be.

    A missing file is not damage: it is every reader's first conversion, and
    it reads as an empty record with no complaint. Everything else that can go
    wrong between here and a parsed record -- a directory the reader cannot
    enter, a file they cannot open, bytes that are not JSON, JSON that is not
    this tool's record -- is reported rather than raised. This function is the
    first thing a conversion calls, and an exception out of it took the whole
    `dbtw convert` down over a state file, with the conversion it refused to
    do correct and complete.
    """
    try:
        if not path.exists():
            return {}, ""
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, _fault(path, str(exc))
    fault = _document_fault(loaded, path)
    if fault:
        return {}, fault
    assert isinstance(loaded, dict)  # _document_fault refuses anything else
    return loaded, ""


def load_progress(project: Path, *, path: Path | None = None) -> Progress:
    """This project's record, or an empty one that says why it is empty.

    Never raises. Every caller reaches this before doing the work the reader
    asked for, and no state of this file is a reason not to do that work.
    """
    store = path if path is not None else state_path()
    loaded, unreadable = _read_file(store)
    if unreadable:
        return Progress(unreadable=unreadable)
    projects = loaded.get("projects")
    if not isinstance(projects, dict):
        return Progress()
    entry = projects.get(_stored_key(projects, project))
    if not isinstance(entry, dict):
        return Progress()
    shown = entry.get("shown")
    conversions = entry.get("conversions")
    return Progress(
        conversions=conversions if isinstance(conversions, int) else 0,
        shown=dict(shown) if isinstance(shown, dict) else {},
    )


@contextmanager
def _exclusive(store: Path) -> Iterator[None]:
    """Hold this record against other writers for the block.

    One file holds every project, and `save_progress` reads it, merges into
    it and writes it back. With nothing held in between, a writer that read
    before another finished wrote a merge computed from a stale snapshot --
    erasing an entire project's curriculum with no error and no warning.
    `dbtw web` left running beside a `dbtw convert` is the ordinary way to
    have two writers, and it was reproducible in under a second.

    Taken on a lock file beside the record rather than on the record itself,
    because the record is replaced by rename on every save and a lock held on
    the old inode would guard nothing. Where locking is not available the
    block still runs: the record is worth a race, and refusing to write it
    would cost a reader their progression to prevent a rare overlap.
    """
    handle = None
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        handle = (store.parent / f"{store.name}.lock").open("a+")
    except OSError:
        # Unwritable directory. The save below raises its own OSError, which
        # its callers turn into a warning -- this is not the place to report.
        yield
        return
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        handle.close()


def save_progress(project: Path, progress: Progress, *, path: Path | None = None) -> None:
    """Write this project's record, leaving every other project's alone.

    Refuses rather than overwrites when the file cannot be read: one file
    holds every project, so writing this run's record over a file we could
    not parse would replace a curriculum a reader is ten conversions into
    with one conversion's worth.
    """
    store = path if path is not None else state_path()
    with _exclusive(store):
        _write_under_lock(store, project, progress)


def _write_under_lock(store: Path, project: Path, progress: Progress) -> None:
    """The read-modify-write itself, with the record held against other writers.

    The read is inside the lock and not before it: a merge computed from a
    snapshot taken before another writer finished is exactly the lost update
    the lock exists to prevent, and doing the read outside would leave the
    lock guarding nothing.
    """
    loaded, unreadable = _read_file(store)
    if unreadable:
        raise ValueError(unreadable)
    projects = loaded.get("projects")
    merged = dict(projects) if isinstance(projects, dict) else {}
    merged[_stored_key(merged, project)] = {
        "conversions": progress.conversions,
        "shown": dict(sorted(progress.shown.items())),
    }
    document = {"version": _FILE_VERSION, "projects": merged}
    store.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the target and moved into place: a half-written record is
    # an unreadable one, and this function's whole contract is that it never
    # leaves the reader with less than they had.
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=store.parent, prefix=store.name, suffix=".tmp", delete=False
    )
    try:
        with handle as out:
            json.dump(document, out, indent=2, sort_keys=True)
            out.write("\n")
        os.replace(handle.name, store)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
