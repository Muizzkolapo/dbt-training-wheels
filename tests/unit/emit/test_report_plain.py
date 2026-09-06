"""The report is where the plain register and the worked example stop being
decorative. Nothing else in production reads `Decision.plain_question`,
`Option.plain`, or `worked_example` -- if the report drops one, these tests
are what notices, rather than the field quietly rotting the way `Option.effect`
did when it was produced at five sites and read by none.
"""

import dataclasses
from pathlib import Path

import pytest

from dbtw.core.assemble import assemble
from dbtw.core.assemble.types import AssembledModel, ProjectChange, SourceEntry
from dbtw.core.context import read_project
from dbtw.core.emit import example as example_module
from dbtw.core.emit import report as report_module
from dbtw.core.emit.example import AFTER_RUN_LABEL, PLACEHOLDER_NOTICE, SUPPOSED_ROW_LABEL
from dbtw.core.emit.report import render_report
from dbtw.core.ingest import classify_statements, ingest
from dbtw.core.passes import run_passes
from dbtw.core.passes.types import Decision, Option, Subject

ROOT = Path(__file__).parents[2] / "fixtures"
PROJECT = ROOT / "projects" / "jaffle_shop"
FIXTURE = ROOT / "sql" / "incremental_etl.sql"

_CTX = read_project(PROJECT)


def _report():
    ir = ingest(FIXTURE, None)
    change = assemble(run_passes(classify_statements(ir), ir.dialect), _CTX)
    return render_report(change, _CTX), change


def _block(report: str, needle: str) -> str:
    """One Decision's whole rendered record: its top-level bullet plus every
    line under it, up to the next Decision or the next section.

    Assertions about what a Decision does *not* render have to be scoped to
    that Decision -- `"|" not in report` would trip over the Models table,
    and `"<customer_id>" not in report` would pass for the wrong reason if
    some other Decision grew an example.
    """
    lines = report.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("- **") and needle in line]
    assert len(starts) == 1, f"{needle!r} matched {len(starts)} decision bullets"
    out = [lines[starts[0]]]
    for line in lines[starts[0] + 1 :]:
        if line.startswith("- **") or line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out)


def _has_no_example(block: str) -> None:
    """No heading without a table, no table without rows, no blank line left
    where a block used to be. A refusal must be invisible, not a gap."""
    assert "Worked example" not in block, block
    assert PLACEHOLDER_NOTICE not in block, block
    assert "matched on" not in block, block
    assert not [line for line in block.splitlines() if line.strip().startswith("|")], block
    assert "" not in block.splitlines(), block
    assert block == block.rstrip(), block


# --- the fixture: an append model with an example, a merge model without one


def test_the_report_prints_the_plain_question_beside_the_dbt_one():
    out, change = _report()
    asked = [d for d in change.decisions if d.plain_question]
    # Without this the loop is vacuous: no plain questions, no assertions.
    assert asked, "the fixture records no plain question at all"
    for d in asked:
        assert d.plain_question in out, f"{d.key}'s plain question is not rendered"


def test_the_report_prints_each_option_s_plain_wording():
    out, change = _report()
    worded = [(d, o) for d in change.decisions for o in d.options if o.plain]
    assert worded, "the fixture offers no option with a plain wording"
    for d, option in worded:
        assert option.plain in out, f"{d.key}/{option.label}'s plain wording is not rendered"


def test_the_report_still_prints_the_dbt_register_beside_the_plain_one():
    """The plain register sits beside the dbt one; it does not replace it.
    A renderer that printed only the plain wording would pass every test
    above and lose the reader who does know dbt."""
    out, change = _report()
    for d in change.decisions:
        if not d.question:
            continue
        assert d.question in out, f"{d.key}'s question is not rendered"
        for option in d.options:
            assert option.effect in out, f"{d.key}/{option.label}'s effect is not rendered"


def test_the_report_prints_a_worked_example_where_one_can_be_built():
    """stg_events projects named columns, so it has an example and the
    report must show it. The placeholder form is what proves it is ours and
    not a claim about the user's data."""
    out, _ = _report()
    assert "<event_id>" in out
    assert "<occurred_at-new>" in out


def test_a_star_projecting_model_gets_no_example_and_no_empty_heading():
    """stg_dim_customers is SELECT *, so no example can be built honestly.
    The report must omit the section, not print an empty one."""
    out, _ = _report()
    assert "<customer_id>" not in out
    _has_no_example(_block(out, "MERGE INTO dim_customers became an incremental model"))


def test_the_example_is_rendered_only_for_the_decision_it_belongs_to():
    """One of the fixture's two questions can be illustrated and the other
    cannot, so exactly one example may appear. Two would mean a Decision was
    paired with a model it did not come from."""
    out, _ = _report()
    assert out.count("Worked example") == 1


def test_the_example_carries_the_notice_the_producing_module_owns():
    """The sentence saying these values were not read from anywhere is a
    claim, not a caption -- it is true because of what `worked_example` is
    given, so `emit.example` owns the wording and the report imports it.
    A renderer authoring its own leaves a second consumer (the web layer
    this plan exists to unblock) with nothing to import and no guarantee it
    would say the same thing.

    Both directions are asserted: every rendered example carries the notice
    (a heading without it fails the count), and a Decision that could not be
    illustrated carries neither -- `_has_no_example` checks the second for
    all six refusal shapes.
    """
    out, _ = _report()
    assert PLACEHOLDER_NOTICE in out
    assert out.count(PLACEHOLDER_NOTICE) == out.count("Worked example")


def test_the_notice_says_nothing_about_where_it_sits_on_the_page():
    """It is engine-owned data, so it has to survive a consumer that puts it
    beside or after its rows. A word like "below" would make it wrong for
    every renderer but this one."""
    for positional in ("below", "above", "following", "shown here"):
        assert positional not in PLACEHOLDER_NOTICE.lower(), positional


def test_the_example_labels_a_supposed_row_and_what_the_model_leaves():
    """Neither block is a fact about the reader's warehouse: the first is a
    premise the reader is asked to grant, the second is what dbt leaves once
    the model runs. And neither may be labelled as what the reader's script
    does -- `worked_example` gets no evidence about the script."""
    out, _ = _report()
    block = _block(out, "INSERT INTO events became an incremental model")
    # Read off the engine rather than spelled here: a renderer that words its
    # own fails this the moment its wording differs from the engine's, which
    # is the failure mode. Spelled literals would pass for both.
    assert SUPPOSED_ROW_LABEL in block, block
    assert AFTER_RUN_LABEL in block, block
    for claim in ("your table", "your warehouse", "your script", "the script leaves"):
        assert claim not in block.lower(), claim
        assert claim not in SUPPOSED_ROW_LABEL.lower(), claim
        assert claim not in AFTER_RUN_LABEL.lower(), claim


def test_the_example_table_lines_up_with_the_model_s_own_columns():
    """The header names the columns the model selects, and every row has one
    cell per column. A row of the wrong width is a table that renders as
    nonsense."""
    out, _ = _report()
    block = _block(out, "INSERT INTO events became an incremental model")
    rows = [line.strip() for line in block.splitlines() if line.strip().startswith("|")]
    assert len(rows) == 6, rows  # header, separator, one before row, three after
    assert rows[0] == "|  | `event_id` | `occurred_at` |"
    assert all(row.count("|") == 4 for row in rows), rows


def test_the_placeholders_are_wrapped_so_a_markdown_renderer_shows_them():
    """`<event_id>` is an HTML tag to a Markdown renderer, and GitHub drops
    it -- the cell would come out empty on the page the team actually reads.
    Backticks are what keep it visible."""
    out, _ = _report()
    assert "`<event_id>`" in out


def test_the_append_example_names_no_key():
    """An append matches nothing, so there is no key to name. A key line
    here would describe a merge the model is not."""
    out, _ = _report()
    block = _block(out, "INSERT INTO events became an incremental model")
    assert "matched on" not in block


def test_the_report_has_no_double_blank_lines():
    """The table needs blank lines around it; nothing else does. Three
    newlines in a row is a gap left by a block that rendered halfway."""
    out, _ = _report()
    assert "\n\n\n" not in out


# --- hand-built changes, for the shapes the fixtures do not reach


def _model(
    body: str = "SELECT\n  customer_id,\n  email\nFROM {{ source('raw', 'customers') }}",
    strategy: str | None = "merge",
    unique_key: tuple[str, ...] = ("customer_id",),
    source_indices: tuple[int, ...] = (1,),
) -> AssembledModel:
    return AssembledModel(
        name="stg_dim_customers",
        path="models/staging/stg_dim_customers.sql",
        body=body,
        materialization="incremental",
        grants=(),
        layer="staging",
        depends_on=(),
        leading_comments=(),
        source_indices=source_indices,
        incremental_strategy=strategy,
        unique_key=unique_key,
    )


def _decision(
    index: int = 1,
    options: tuple[Option, ...] = (
        Option(
            label="merge on customer_id", effect="updates the matched row", plain="says it again"
        ),
    ),
) -> Decision:
    return Decision(
        key=f"tier2.merge.in.sql:{index}",
        tier=2,
        action="dim_customers becomes an incremental model",
        reason="the script's MERGE names a key",
        source_file="in.sql",
        line_start=1,
        line_end=4,
        question="does customer_id uniquely identify a row in dim_customers?",
        plain_question="is there only ever one row for each customer_id?",
        chosen="merge on customer_id",
        options=options,
        subject=Subject(table="dim_customers", columns=("customer_id",)),
    )


# Rendered after the Decision under test so its block is always terminated by
# another bullet rather than by the end of the section. Without it, the blank
# line the report puts between sections lands inside the extracted block and
# `_has_no_example`'s stray-blank-line check can never fail.
_SENTINEL = Decision(
    key="tier2.merge.no_insert_branch.in.sql:1",
    tier=2,  # the same tier, so it renders after rather than before
    action="caveat: dim_customers has no WHEN NOT MATCHED branch",
    reason="the converted model inserts rows the script never did",
    source_file="in.sql",
    line_start=1,
    line_end=4,
)


def _hand_report(model: AssembledModel, decision: Decision) -> str:
    change = ProjectChange(
        models=(model,),
        sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),),
        decisions=(decision, _SENTINEL),
        pending=(),
        dialect="tsql",
        project_name="jaffle_shop",
        variables=(),
    )
    return render_report(change, _CTX)


def test_a_merge_example_names_the_key_it_matches_on():
    """The merge branch, which no fixture reaches -- the only merge model in
    the fixtures is a SELECT *. Without the key named, the two after-rows
    look arbitrary: nothing on the page says why one row's first cell held
    its value and the other's did not."""
    out = _hand_report(_model(), _decision())
    assert "matched on customer_id" in out
    assert "`<customer_id>` | `<email-new>`" in out  # the row found by its key and updated
    assert "`<customer_id-new>` | `<email-new>`" in out  # the row matching nothing, inserted


REFUSALS = {
    "star projection": _model(body="SELECT\n  *\nFROM {{ source('raw', 'customers') }}"),
    "unparseable body": _model(body="SELEC customer_id FROM"),
    "unnamed projection": _model(
        body="SELECT\n  customer_id,\n  COUNT(*)\nFROM {{ source('raw', 'customers') }}",
        unique_key=("customer_id",),
    ),
    "not incremental": _model(strategy=None, unique_key=()),
    "merge with no key": _model(unique_key=()),
    # Every column is named and the key is projected, so the line break in
    # the second alias is the only thing left to refuse on.
    "line break in a column name": _model(
        body="SELECT\n  customer_id,\n  email AS \"a\nb\"\nFROM {{ source('raw', 'customers') }}"
    ),
}


@pytest.mark.parametrize("model", REFUSALS.values(), ids=list(REFUSALS))
def test_a_model_that_cannot_be_illustrated_renders_nothing_at_all(model: AssembledModel):
    _has_no_example(_block(_hand_report(model, _decision()), "dim_customers becomes"))


def test_a_decision_from_a_statement_no_model_was_built_from_renders_nothing():
    """The pairing rule: statement index against the model's source_indices.
    The names agree here -- the Decision's subject and the model both say
    dim_customers -- and the statement indices do not, so nothing may be
    drawn."""
    out = _hand_report(_model(source_indices=(7,)), _decision(index=1))
    _has_no_example(_block(out, "dim_customers becomes"))


def test_the_decision_is_paired_by_statement_index_not_by_name():
    """A script converting both `orders` and `orders_archive` gives one
    Decision the subject `orders` and the project two models whose names
    both contain it. Names cannot tell them apart -- Subject.table is the
    script's spelling and model.name is the renamed model, and the
    assembler records that a collision gives two statements one model name
    besides. Matching on the name would hand `worked_example` the archive
    model, which is not the one this statement became; it would refuse, and
    the reader would lose an example that could honestly have been drawn.
    """
    archive = dataclasses.replace(
        _model(source_indices=(9,)),
        name="stg_orders_archive",
        body="SELECT\n  order_id,\n  archived_at\nFROM {{ source('raw', 'orders') }}",
        unique_key=("order_id",),
    )
    orders = dataclasses.replace(
        _model(source_indices=(1,)),
        name="stg_orders",
        body="SELECT\n  order_id,\n  amount\nFROM {{ source('raw', 'orders') }}",
        unique_key=("order_id",),
    )
    decision = dataclasses.replace(
        _decision(index=1), subject=Subject(table="orders", columns=("order_id",))
    )
    change = ProjectChange(
        models=(archive, orders),  # the archive is reached first by any name scan
        sources=(SourceEntry(source_name="raw", schema="raw", table="orders"),),
        decisions=(decision, _SENTINEL),
        pending=(),
        dialect="tsql",
        project_name="jaffle_shop",
        variables=(),
    )
    block = _block(render_report(change, _CTX), "dim_customers becomes")
    assert "`<amount-new>`" in block, block  # stg_orders, the model it became
    assert "archived_at" not in block, block  # stg_orders_archive, which it did not


def test_an_example_that_ends_the_section_leaves_no_gap_behind_it():
    """The example closes on a blank line so its table is not run into the
    next bullet. When it is the last thing in the section, that blank meets
    the one between sections and leaves a two-line hole under the report's
    last Decision."""
    change = ProjectChange(
        models=(_model(),),
        sources=(SourceEntry(source_name="raw", schema="raw", table="customers"),),
        decisions=(_decision(),),
        pending=(),
        dialect="tsql",
        project_name="jaffle_shop",
        variables=(),
    )
    out = render_report(change, _CTX)
    assert "matched on customer_id" in out  # the example really is the last thing
    assert "\n\n\n" not in out


def test_a_question_with_no_options_renders_no_empty_answers_heading():
    """The same rule the example follows: a heading with nothing under it is
    worse than no heading, because it reads as a rendering bug."""
    out = _hand_report(_model(), _decision(options=()))
    block = _block(out, "dim_customers becomes")
    assert "Question:" in block
    assert "Answers:" not in block


def test_a_column_name_containing_a_pipe_does_not_break_the_table():
    """A quoted alias can legally contain the character Markdown uses to end
    a cell. Unescaped, every row after it is one cell short and the table
    renders as nonsense."""
    body = "SELECT\n  customer_id,\n  email AS \"a|b\"\nFROM {{ source('raw', 'customers') }}"
    out = _hand_report(_model(body=body), _decision())
    rows = [line.strip() for line in out.splitlines() if line.strip().startswith("| ")]
    example_rows = [row for row in rows if "customer_id" in row or "<" in row]
    assert example_rows, out
    assert all(row.count("|") - row.count("\\|") == 4 for row in example_rows), example_rows


def test_a_column_name_containing_a_backtick_stays_inside_its_code_span():
    """Every cell is wrapped in backticks so `<customer_id>` is not read as an
    HTML tag and dropped. A backtick in the name closes that span early and
    the rest of the cell -- the placeholder's own closing bracket included --
    spills into the prose beside it. No backslash escape reaches inside a code
    span, so the fence is widened instead, which is Markdown's own mechanism
    for holding one."""
    body = "SELECT\n  customer_id,\n  email AS \"a`b\"\nFROM {{ source('raw', 'customers') }}"
    out = _hand_report(_model(body=body), _decision())
    assert "``a`b``" in out  # the header cell
    assert "``<a`b>``" in out  # the row already there
    assert "``<a`b-new>``" in out  # the row the model leaves

    # The plain columns are untouched by the widening -- a single backtick is
    # still enough for a name that contains none.
    assert "`<customer_id>`" in out


def test_the_header_is_protected_the_same_way_the_cells_under_it_are():
    """A quoted alias can itself be an HTML tag (`SELECT email AS "<b>"`), and
    the header takes its names from the same SQL the placeholders below it are
    built over. Left bare, GitHub drops the tag and the column is nameless
    above cells that still show -- the same failure the backticks around a
    value cell exist to prevent, one row up."""
    body = "SELECT\n  customer_id,\n  email AS \"<b>\"\nFROM {{ source('raw', 'customers') }}"
    block = _block(_hand_report(_model(body=body), _decision()), "dim_customers becomes")
    rows = [line.strip() for line in block.splitlines() if line.strip().startswith("|")]
    assert rows[0] == "|  | `customer_id` | `<b>` |", rows
    # Not vacuous: the cells under it carry the same name and are wrapped too,
    # so the header is not the odd one out in either direction.
    assert "`<<b>>`" in block, block


def test_the_report_reads_the_row_labels_at_render_time_rather_than_copying_them(monkeypatch):
    """The direction that matters, and the one an "is the wording there?"
    assertion cannot reach: it passes just as well for a renderer that has
    re-typed the same words.

    Both names are replaced on the *report* module and the report re-rendered.
    A report that authors its own copies renders the original wording and
    fails; a report with no such names at all fails `setattr` outright; only
    one that reads the engine's strings at render time renders the sentinels.
    That is the proof the two labels are wired rather than coincidentally
    matching -- and the reason it is worth having is that "after your script
    runs", authored in a consumer, is exactly the claim deleted from this
    branch two rounds ago for being evidence the engine does not hold.
    """
    out, _ = _report()
    assert SUPPOSED_ROW_LABEL in out and AFTER_RUN_LABEL in out

    monkeypatch.setattr(report_module, "SUPPOSED_ROW_LABEL", "<<premise>>")
    monkeypatch.setattr(report_module, "AFTER_RUN_LABEL", "<<result>>")
    mutated, _ = _report()

    assert "<<premise>>" in mutated, mutated
    assert "<<result>>" in mutated, mutated
    # The engine's own wording is gone with it, so nothing rendered a copy.
    assert SUPPOSED_ROW_LABEL not in mutated, mutated
    assert AFTER_RUN_LABEL not in mutated, mutated


def test_the_example_surface_is_reachable_from_the_package():
    """`Example`, `worked_example`, and the three strings an example must be
    rendered under are how a second consumer -- the web layer this branch
    exists to unblock -- gets them. Exposed on the package like every other
    public name in `dbtw.core.*`, so that consumer imports the wording rather
    than reaching past the package into a submodule or, worse, writing its
    own."""
    import dbtw.core.emit as emit

    assert emit.Example is example_module.Example
    assert emit.worked_example is example_module.worked_example
    assert emit.PLACEHOLDER_NOTICE == example_module.PLACEHOLDER_NOTICE
    assert emit.SUPPOSED_ROW_LABEL == example_module.SUPPOSED_ROW_LABEL
    assert emit.AFTER_RUN_LABEL == example_module.AFTER_RUN_LABEL

    exported = set(emit.__all__)
    for name in (
        "Example",
        "worked_example",
        "PLACEHOLDER_NOTICE",
        "SUPPOSED_ROW_LABEL",
        "AFTER_RUN_LABEL",
    ):
        assert name in exported, name
        assert getattr(emit, name) is not None, name
