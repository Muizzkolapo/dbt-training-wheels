"""The suite never reads or writes the reader's own record of explained words.

Found because parallel runs of the suite failed now and then. Only two test
files pointed the record somewhere temporary; every other test that converts
through `dbtw convert` or the walk read and wrote the real file under the
user's state directory. Two runs at once then changed each other's reports --
a word explained in full in one run was folded in the other -- and the user's
own record filled with throwaway project paths (3 MB of them).
"""

from __future__ import annotations

from pathlib import Path

from dbtw.core.progress import state_path


def test_the_record_points_inside_a_temporary_directory_during_tests(tmp_path: Path) -> None:
    path = state_path()

    assert not path.is_relative_to(Path.home() / ".local" / "state"), path
    assert path.is_relative_to(tmp_path), path
