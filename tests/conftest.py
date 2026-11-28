"""Suite-wide fixtures.

One rule lives here because it holds for every test: no test reads or writes
the reader's own state directory. `dbtw` keeps a record of which dbt words it
has explained (`dbtw.core.progress`) under `XDG_STATE_HOME`, falling back to
`~/.local/state`. A test that converts through the CLI or the walk without
redirecting it wrote the user's real record -- and two runs of the suite at
once changed each other's reports, which is how this was found.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _own_state_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the record at this test's own temporary directory.

    Per test rather than per session, so one test's record cannot fold the
    words of the next. A test that sets `XDG_STATE_HOME` itself still wins:
    its `monkeypatch.setenv` runs after this one.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
