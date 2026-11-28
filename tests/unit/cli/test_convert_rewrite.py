"""`dbtw convert` run twice into one --out.

The script grows a model that reads `revenue_daily`, so the second run
places `revenue_daily` in intermediate where the first placed it in marts.
The --out directory must then hold it once, in intermediate, and the
terminal must say what was removed.
"""

from __future__ import annotations

from pathlib import Path

from dbtw.cli.main import main

PROJECT = Path(__file__).parents[2] / "fixtures" / "projects" / "three_layers"

FIRST = (
    "INSERT INTO revenue_events SELECT order_id, amount FROM raw_orders;\n"
    "INSERT INTO revenue_daily SELECT order_id, amount FROM revenue_events;\n"
)
SECOND = FIRST + "INSERT INTO revenue_weekly SELECT order_id, amount FROM revenue_daily;\n"


def _convert(sql: Path, out: Path) -> int:
    return main(
        ["convert", str(sql), "--project", str(PROJECT), "--out", str(out), "--no-remember"]
    )


def test_a_second_convert_into_one_out_removes_the_model_from_the_layer_it_left(
    tmp_path: Path, capsys
) -> None:
    sql = tmp_path / "sql" / "in.sql"
    sql.parent.mkdir()
    out = tmp_path / "out"
    sql.write_text(FIRST, encoding="utf-8")
    assert _convert(sql, out) == 0
    assert (out / "models" / "marts" / "revenue_daily.sql").is_file()
    capsys.readouterr()

    sql.write_text(SECOND, encoding="utf-8")
    assert _convert(sql, out) == 0

    assert not (out / "models" / "marts" / "revenue_daily.sql").exists()
    assert (out / "models" / "intermediate" / "revenue_daily.sql").is_file()
    assert (out / "models" / "marts" / "revenue_weekly.sql").is_file()
    err = capsys.readouterr().err
    assert "note: removed models/marts/revenue_daily.sql" in err


def test_a_file_of_the_readers_own_in_out_survives_a_second_convert(tmp_path: Path) -> None:
    sql = tmp_path / "sql" / "in.sql"
    sql.parent.mkdir()
    out = tmp_path / "out"
    sql.write_text(FIRST, encoding="utf-8")
    assert _convert(sql, out) == 0
    mine = out / "models" / "marts" / "mine.sql"
    mine.write_text("select 'mine'\n", encoding="utf-8")

    sql.write_text(SECOND, encoding="utf-8")
    assert _convert(sql, out) == 0

    assert mine.read_text(encoding="utf-8") == "select 'mine'\n"


def test_a_stale_file_the_reader_edited_is_a_usage_error_not_a_traceback(
    tmp_path: Path, capsys
) -> None:
    sql = tmp_path / "sql" / "in.sql"
    sql.parent.mkdir()
    out = tmp_path / "out"
    sql.write_text(FIRST, encoding="utf-8")
    assert _convert(sql, out) == 0
    edited = out / "models" / "marts" / "revenue_daily.sql"
    edited.write_text("-- mine now\n", encoding="utf-8")
    capsys.readouterr()

    sql.write_text(SECOND, encoding="utf-8")
    assert _convert(sql, out) == 2

    assert "models/marts/revenue_daily.sql" in capsys.readouterr().err
    assert edited.read_text(encoding="utf-8") == "-- mine now\n"
