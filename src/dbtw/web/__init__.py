"""The browser surface, behind an optional extra: `pip install
'dbt-training-wheels[web]'`.

Importing this package does not import Flask. The session is ordinary core
code and has to stay usable -- and testable -- in an install without the
extra; `require_flask` is the one place that asks whether the extra is there,
and it asks at the moment a command that needs it starts.
"""

from __future__ import annotations

from dbtw.web.state import EmptySourceError, Session, SessionView, Source

__all__ = [
    "EmptySourceError",
    "MissingWebExtraError",
    "Session",
    "SessionView",
    "Source",
    "require_flask",
]

_EXTRA = "pip install 'dbt-training-wheels[web]'"


class MissingWebExtraError(ImportError):
    """`dbtw web` was run in an install without the web extra.

    A usage error, and a member of the CLI's `_USAGE_ERRORS`: the user's
    command cannot work as given, and that is not a dbtw bug. It is the
    opposite category to `DuplicateSourceEntryError` and
    `MulticolumnCheckedAnswerError`, which are kept out of that tuple
    precisely because no input can produce them, so reaching one means a bug
    and should surface as a traceback. An environment missing an optional
    dependency is input, in the only sense that matters here -- something the
    user can change, told what to change it to.

    An ImportError rather than the ValueError the other usage errors
    subclass, because that is what it is: the message adds the remedy that a
    bare ModuleNotFoundError cannot know.
    """


def require_flask() -> None:
    """Refuse now, naming the extra, if Flask cannot be imported.

    Catches ImportError rather than ModuleNotFoundError: a half-installed or
    broken Flask fails the same command for the same practical reason, and
    the original error travels in the message so a broken install is not
    reported as a missing one.
    """
    try:
        import flask  # noqa: F401
    except ImportError as exc:
        raise MissingWebExtraError(
            f"dbtw web needs Flask, which could not be imported ({exc}). "
            f"Install the web extra: {_EXTRA}"
        ) from exc
