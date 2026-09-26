"""A word this project has already explained three times stops being explained.

The tool is stateless, so its tenth conversion teaches exactly what its first
did: the same glossary, the same definitions, in the same place. The design's
own research says four of five readers never reached a glossary block --
repeating it verbatim ten times is how the fifth stops reading it too.

What a reader has already been shown is a fact about *them*, not about their
SQL, so it is kept beside them (one file, per project) rather than in the
conversion. Nothing here decides anything from a count it did not read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dbtw.core.progress import (
    FULL_SHOWINGS,
    Progress,
    after,
    load_progress,
    save_progress,
    teaching_for,
)
from dbtw.core.teach import GLOSSARY, terms_in

REF = next(t for t in GLOSSARY if t.name == "ref()")
SOURCE = next(t for t in GLOSSARY if t.name == "source()")


def test_a_reader_with_no_record_is_shown_everything() -> None:
    """The honest default, and the first conversion of every project. A word
    nothing says was explained has not been explained.
    """
    teaching = teaching_for((REF, SOURCE), Progress())

    assert teaching.full == (REF, SOURCE)
    assert teaching.met == ()


def test_a_word_is_explained_in_full_up_to_the_threshold() -> None:
    teaching = teaching_for((REF,), Progress(shown={"ref()": FULL_SHOWINGS - 1}))

    assert teaching.full == (REF,)
    assert teaching.met == ()


def test_a_word_met_enough_times_is_moved_out_of_the_way_not_deleted() -> None:
    """Folded, never dropped: a word she met three runs ago is still a word
    she may have forgotten, and the meaning has to stay reachable in the
    report in front of her rather than in one she no longer has.
    """
    teaching = teaching_for((REF, SOURCE), Progress(shown={"ref()": FULL_SHOWINGS}))

    assert teaching.full == (SOURCE,)
    assert teaching.met == (REF,)


def test_order_is_the_order_the_reader_meets_them() -> None:
    teaching = teaching_for((SOURCE, REF), Progress())

    assert teaching.full == (SOURCE, REF)


def test_recording_counts_only_the_words_this_conversion_showed() -> None:
    """A word counts when it was put in front of her, not when it exists.
    Counting the whole glossary would retire words she has never seen.
    """
    progress = after((REF,), Progress())

    assert progress.shown == {"ref()": 1}
    assert progress.conversions == 1
    assert "source()" not in progress.shown


def test_recording_a_folded_word_does_not_keep_counting_it() -> None:
    """Past the threshold the count stops moving. It is a record of what was
    explained, and a folded word was not explained again -- a number that went
    on rising would claim showings that never happened.
    """
    at_threshold = Progress(conversions=9, shown={"ref()": FULL_SHOWINGS})

    progress = after((REF,), at_threshold)

    assert progress.shown["ref()"] == FULL_SHOWINGS
    assert progress.conversions == 10


def test_a_record_survives_a_round_trip(tmp_path: Path) -> None:
    store = tmp_path / "learned.json"
    project = tmp_path / "analytics"

    save_progress(project, Progress(conversions=2, shown={"ref()": 2}), path=store)

    assert load_progress(project, path=store) == Progress(conversions=2, shown={"ref()": 2})


def test_two_projects_are_learned_separately(tmp_path: Path) -> None:
    """Her team's project and her own are two curricula. A word explained in
    one says nothing about what she has met in the other, and one shared
    count would retire a word in a project she has never converted into.
    """
    store = tmp_path / "learned.json"
    save_progress(tmp_path / "one", Progress(conversions=3, shown={"ref()": 3}), path=store)
    save_progress(tmp_path / "two", Progress(conversions=1, shown={"source()": 1}), path=store)

    assert load_progress(tmp_path / "one", path=store).shown == {"ref()": 3}
    assert load_progress(tmp_path / "two", path=store).shown == {"source()": 1}


def test_a_missing_record_is_a_first_conversion_not_an_error(tmp_path: Path) -> None:
    assert load_progress(tmp_path / "analytics", path=tmp_path / "nothing.json") == Progress()


def test_an_unreadable_record_says_so_and_explains_everything(tmp_path: Path) -> None:
    """The one thing this must not do is treat a damaged record as an empty
    one in silence: the reader would be told nothing while the tool quietly
    forgot what it had taught. Everything is explained in full -- which is the
    safe direction -- and the report says the record could not be read.
    """
    store = tmp_path / "learned.json"
    store.write_text("{not json at all", encoding="utf-8")

    progress = load_progress(tmp_path / "analytics", path=store)

    assert progress.shown == {}
    assert progress.unreadable, "a damaged record has to be announced"
    assert str(store) in progress.unreadable
    assert teaching_for((REF,), progress).full == (REF,)


def test_saving_never_loses_another_projects_record(tmp_path: Path) -> None:
    """One file holds every project, so a write has to merge rather than
    replace -- overwriting would silently reset a curriculum she is ten
    conversions into.
    """
    store = tmp_path / "learned.json"
    save_progress(tmp_path / "one", Progress(conversions=9, shown={"ref()": 3}), path=store)

    save_progress(tmp_path / "two", Progress(conversions=1, shown={"source()": 1}), path=store)

    assert load_progress(tmp_path / "one", path=store).conversions == 9


def test_a_damaged_record_is_never_written_over(tmp_path: Path) -> None:
    """Refusing to save is the point: a reader whose file is damaged keeps
    what is in it until they look, rather than having ten conversions of
    progress replaced by this one run's.
    """
    store = tmp_path / "learned.json"
    store.write_text("{not json at all", encoding="utf-8")

    with pytest.raises(ValueError, match="could not be read"):
        save_progress(tmp_path / "analytics", Progress(conversions=1), path=store)

    assert store.read_text(encoding="utf-8") == "{not json at all"


def test_the_record_is_plain_readable_json(tmp_path: Path) -> None:
    """She has to be able to see what this tool believes about her, and
    delete it. That means a file a person can read, not a pickle or a cache.
    """
    store = tmp_path / "learned.json"

    save_progress(tmp_path / "analytics", Progress(conversions=1, shown={"ref()": 1}), path=store)

    written = json.loads(store.read_text(encoding="utf-8"))
    assert "ref()" in json.dumps(written)


def test_every_glossary_term_can_be_recorded() -> None:
    """The record is keyed by `Term.name`, so a term whose name is not a
    usable key would be silently untrackable -- explained forever, or never.
    """
    progress = after(tuple(GLOSSARY), Progress())

    assert set(progress.shown) == {term.name for term in GLOSSARY}


def test_the_words_a_text_uses_drive_the_teaching() -> None:
    """`terms_in` stays the one thing that decides which words are in play;
    this module only decides how much of each to say.
    """
    text = "dbt writes it with ref() and declares the rest with source()."

    teaching = teaching_for(terms_in(text), Progress(shown={"ref()": FULL_SHOWINGS}))

    assert [t.name for t in teaching.full] == ["source()"]
    assert [t.name for t in teaching.met] == ["ref()"]


def test_an_unreachable_record_is_never_fatal(tmp_path: Path) -> None:
    """Found by review: `path.exists()` does not swallow PermissionError, and
    `load_progress` is the first thing a conversion calls. A state directory
    the reader cannot read took the whole `dbtw convert` down -- exit 2, zero
    files written -- over a side concern, while the conversion it refused to
    do was correct and complete.
    """
    store = tmp_path / "locked" / "learned.json"
    store.parent.mkdir(parents=True)
    store.write_text("{}", encoding="utf-8")
    store.parent.chmod(0o000)
    try:
        progress = load_progress(tmp_path / "analytics", path=store)
    finally:
        store.parent.chmod(0o755)

    assert progress.shown == {}
    assert progress.unreadable, "unreachable is a kind of unreadable, and it is announced"


def test_a_record_whose_shape_is_wrong_is_unreadable_not_empty(tmp_path: Path) -> None:
    """The guard's coverage set, found too narrow by review. A file that
    parses as JSON but is not this tool's record was being coerced to
    "nothing explained" -- which is the one reading that looks exactly like a
    first conversion and says nothing.
    """
    store = tmp_path / "learned.json"
    for bad in (
        '{"projects": "not a mapping"}',
        '{"projects": {"KEY": "not an entry"}}',
        '{"projects": {"KEY": {"conversions": "many"}}}',
        '{"projects": {"KEY": {"shown": []}}}',
        '{"projects": {"KEY": {"shown": {"ref()": 1.5}}}}',
        '{"projects": {"KEY": {"shown": {"ref()": -7}}}}',
    ):
        store.write_text(bad.replace("KEY", str((tmp_path / "analytics").resolve())), "utf-8")

        progress = load_progress(tmp_path / "analytics", path=store)

        assert progress.unreadable, f"silently read as empty: {bad}"
        assert progress.shown == {}


def test_a_record_from_a_newer_version_is_not_half_read_and_overwritten(tmp_path: Path) -> None:
    """The worst way to lose a curriculum: a later version writes a field this
    one does not understand, this one reads what it recognises, and the next
    save replaces the file with the part it understood.
    """
    store = tmp_path / "learned.json"
    key = str((tmp_path / "analytics").resolve())
    store.write_text(f'{{"version": 99, "projects": {{"{key}": {{"conversions": 4}}}}}}', "utf-8")

    progress = load_progress(tmp_path / "analytics", path=store)

    assert progress.unreadable
    with pytest.raises(ValueError):
        save_progress(tmp_path / "analytics", Progress(conversions=1), path=store)
    assert '"version": 99' in store.read_text(encoding="utf-8")


def test_one_project_reached_by_two_spellings_is_one_curriculum(tmp_path: Path) -> None:
    """On a case-insensitive filesystem (macOS, Windows) `Project` and
    `project` are one directory, and `Path.resolve()` keeps whichever spelling
    was typed -- so the same project got two records and her curriculum
    silently restarted when she typed it differently. Identity is which file
    it is, not how it was spelled.
    """
    project = tmp_path / "CaseTest"
    project.mkdir()
    store = tmp_path / "learned.json"
    save_progress(project, Progress(conversions=3, shown={"ref()": 3}), path=store)

    other_spelling = tmp_path / "casetest"
    if not other_spelling.exists():  # a case-sensitive filesystem: two real directories
        pytest.skip("case-sensitive filesystem: the two spellings are two directories")

    assert load_progress(other_spelling, path=store).conversions == 3


def test_two_writers_at_once_do_not_lose_each_others_projects(tmp_path: Path) -> None:
    """Found by review, reproduced: `save_progress` reads, merges and writes
    with nothing holding the file in between, so a writer that read before
    another writer finished wrote a merge computed from a stale snapshot --
    erasing an entire project's curriculum with no error and no warning. One
    file holds every project, and `dbtw web` left running beside a
    `dbtw convert` is the ordinary way to have two writers.
    """
    import subprocess
    import sys
    import textwrap

    store = tmp_path / "learned.json"
    script = textwrap.dedent("""
        import sys
        from pathlib import Path
        from dbtw.core.progress import Progress, save_progress
        store, project = Path(sys.argv[1]), Path(sys.argv[2])
        for n in range(1, 41):
            save_progress(project, Progress(conversions=n, shown={"ref()": 1}), path=store)
    """)
    runner = tmp_path / "writer.py"
    runner.write_text(script, encoding="utf-8")
    env = {"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"}
    both = [
        subprocess.Popen([sys.executable, str(runner), str(store), str(tmp_path / name)], env=env)
        for name in ("one", "two")
    ]
    for process in both:
        assert process.wait(timeout=60) == 0

    assert load_progress(tmp_path / "one", path=store).conversions == 40
    assert load_progress(tmp_path / "two", path=store).conversions == 40
