"""Renders the human-facing conversion report. No I/O — text in, text out."""

from __future__ import annotations

import re

import sqlglot
from sqlglot.errors import SqlglotError

from dbtw.core.assemble import ProjectChange
from dbtw.core.context import ProjectContext
from dbtw.core.emit.example import (
    AFTER_RUN_LABEL,
    PLACEHOLDER_NOTICE,
    SUPPOSED_ROW_LABEL,
    Example,
    worked_example,
)
from dbtw.core.naming import is_atomic_sql
from dbtw.core.passes.types import Decision, statement_index
from dbtw.core.teach import terms_in

_NOT_DONE_YET = """\
## Not done yet

This report describes a mechanical conversion only. No validation has run —
dbt was not invoked, nothing was compiled, and nothing was run against a
warehouse.

Table references and script variables have been rewritten as ref(), source(),
and var() calls. Incremental models, temporary tables, and UPDATE or other
multi-statement rewrites remain deferred. Tests, documentation blocks, and
exposures were not generated. Review the model bodies and the decisions above
before treating any of this as final.\
"""


def render_report(change: ProjectChange, ctx: ProjectContext) -> str:
    summary = _render_summary(change)
    sections = [
        _render_conventions(ctx),
        _render_models(change),
        _render_sources(change),
    ]
    if change.variables:
        sections.append(_render_vars(change))
    sections.extend(
        [
            _render_decisions(change),
            _render_pending(change),
            _NOT_DONE_YET,
        ]
    )
    # Second, not last. The glossary is read off the report rather than
    # authored, so it has to be built after the rest of the report exists --
    # but it is placed before the first section that uses any of the words.
    # The conventions section below already says "materialization" and
    # "staging", and a reader who has to go looking for a definition has
    # already been shown to give up: four of five personas never reached the
    # round-1 glossary, which sat at the end.
    glossary = _render_glossary("\n\n".join([summary, *sections]))
    return "\n\n".join([summary, glossary, *sections]) + "\n"


def _render_summary(change: ProjectChange) -> str:
    dialect = change.dialect if change.dialect is not None else "not specified"
    lines = [
        "## Summary",
        "",
        f"- **Project**: {change.project_name}",
        f"- **Models**: {len(change.models)}",
        f"- **Tests**: {len(change.tests)}",
        f"- **Sources**: {len(change.sources)}",
        f"- **Pending statements**: {len(change.pending)}",
        f"- **Dialect**: {dialect}",
    ]
    return "\n".join(lines)


_GLOSSARY_HEADING = "## Words this report uses"
_GLOSSARY_INTRO = (
    "Each dbt word this report uses, in the order it first appears. "
    "A word the report does not use is not listed here."
)


def _render_glossary(body: str) -> str:
    """The dbt words `body` uses, defined, and no others.

    Read off the assembled report rather than authored, for the reason the
    section exists at all: a hand-kept list is a claim about what the report
    says, and it goes stale the first time a Decision is reworded. This one
    cannot say `dbt build` while the report never mentions it, and cannot
    leave `materialized` undefined while the models table prints it.

    There is no empty case to handle: `_NOT_DONE_YET` is rendered by every
    report and names four of these words on its own -- see
    test_the_closing_section_alone_guarantees_the_glossary_is_never_empty.
    """
    defined = [f"- **{term.name}** — {term.plain}" for term in terms_in(body)]
    return "\n".join([_GLOSSARY_HEADING, "", _GLOSSARY_INTRO, "", *defined])


def _render_conventions(ctx: ProjectContext) -> str:
    lines = ["## Your project's conventions", ""]
    if not ctx.detections:
        lines.append("No conventions were detected.")
        return "\n".join(lines)
    for d in ctx.detections:
        value = d.value if d.value is not None else "undetermined"
        lines.append(f"- **{d.key}**: {value} — {d.evidence}")
    return "\n".join(lines)


def _render_models(change: ProjectChange) -> str:
    lines = ["## Models", ""]
    if not change.models:
        lines.append("No models were produced.")
        return "\n".join(lines)
    lines.append("| Model | Layer | Materialization | Depends on |")
    lines.append("| --- | --- | --- | --- |")
    for m in change.models:
        mat = m.materialization if m.materialization is not None else "(layer default)"
        deps = ", ".join(m.depends_on) if m.depends_on else "—"
        lines.append(f"| {m.name} | {m.layer} | {mat} | {deps} |")
    return "\n".join(lines)


def _render_sources(change: ProjectChange) -> str:
    lines = ["## Sources", ""]
    if not change.sources:
        lines.append("None to declare.")
        return "\n".join(lines)
    for s in change.sources:
        lines.append(f"- **{s.source_name}**.{s.table} (schema: {s.schema})")
    return "\n".join(lines)


def _yaml_double_quoted(text: str) -> str:
    """Wrap `text` as a YAML double-quoted scalar whose *content* is `text`
    verbatim, backslash/double-quote escaped so the scalar stays well-formed.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parenthesized_if_compound(default_sql: str, dialect: str | None) -> str:
    """A compound default (`1 + 2`) must reach `var()` with the same
    defensive parens `--inline-vars` would give it — `naming.is_atomic_sql`
    is the one shared rule both paths use, so they can never disagree on
    what counts as "needs parens" (FINDING 7: `{{ var('n') }} * 3` and an
    inlined `(1 + 2) * 3` must compute the same thing for the same `n`).
    An unparseable default_sql (extraction should never actually produce
    one, since it always comes from a node that did parse) degrades to the
    unwrapped text rather than crashing report rendering.
    """
    try:
        node = sqlglot.parse_one(default_sql, read=dialect)
    except SqlglotError:
        return default_sql
    if is_atomic_sql(node):
        return default_sql
    return f"({default_sql})"


def _render_vars(change: ProjectChange) -> str:
    # default_sql is the raw SQL literal text (e.g. `'2024-01-01'`, quotes
    # included for a string default). It must reach `var()` byte-identical to
    # what --inline-vars would splice into the SQL — that's the whole point
    # of keeping a variable instead of inlining it. Interpolating it bare
    # (`{name}: {default_sql}`) puts it in YAML's *plain* scalar syntax,
    # where a leading `'` starts a *single-quoted* scalar and YAML strips
    # those quotes on load: `start_date: '2024-01-01'` loads as the bare
    # string `2024-01-01`, and dbt renders that unquoted into the compiled
    # SQL — `WHERE order_date >= 2024-01-01` is integer arithmetic, not a
    # date comparison (FINDING 1, proven with a real `dbt compile`).
    #
    # The fix: wrap default_sql in a YAML *double*-quoted scalar, so its
    # content — the SQL literal, quotes and all — survives the YAML round
    # trip intact and var() renders exactly what --inline-vars would inline.
    # See test_vars_block_stays_parseable_yaml_for_awkward_defaults.
    lines = [
        "## Add to your dbt_project.yml",
        "",
        "These are the script variables the conversion turned into `var()` calls. "
        "This is a fragment to merge into your project's existing `dbt_project.yml`, "
        "not a replacement for it.",
        "",
        "```yaml",
        "vars:",
    ]
    for v in change.variables:
        if v.default_sql is None:
            lines.append(f"  {v.name}:  # no default in the source; set one")
        else:
            value = _parenthesized_if_compound(v.default_sql, change.dialect)
            lines.append(f"  {v.name}: {_yaml_double_quoted(value)}")
    lines.append("```")
    return "\n".join(lines)


# A pipe inside a table cell, escaped so Markdown reads it as content rather
# than as the end of the cell. Named rather than inlined because a backslash
# is not allowed inside an f-string expression before Python 3.12, and this
# package supports 3.11.
_ESCAPED_PIPE = "\\|"

# Every run of backticks in a cell's text, so `_example_cell` can choose a
# fence longer than the longest of them.
_BACKTICK_RUN = re.compile(r"`+")


def _escaped(text: str) -> str:
    """`text` with any pipe escaped, so a quoted alias that contains one
    (`SELECT email AS "a|b"`) stays inside its cell instead of ending it and
    shifting every later cell in the row one column left.

    A pipe is the only one of its class this can fix, and the other two are
    handled where they can be:

    * a backtick would close the code span `_example_cell` wraps a cell in,
      and no backslash escape reaches it -- backslash escapes are inert
      inside a code span. `_example_cell` widens the fence instead, which is
      Markdown's own mechanism for holding a backtick.
    * a line break ends the row wherever it falls and nothing escapes it, so
      `worked_example` refuses to build an example over a column named with
      one at all -- `emit.example._LINE_BREAKS`.
    """
    return text.replace("|", _ESCAPED_PIPE)


def _example_cell(text: str) -> str:
    """One cell -- header or value alike: escaped, and wrapped as inline code.

    The backticks are not decoration. A placeholder like `<event_id>` is an
    HTML tag to a Markdown renderer, and GitHub drops it -- the cell arrives
    empty on the page the team actually reads. The header runs through this
    for exactly the same reason and not a weaker one: its column names come
    from the same SQL as the placeholders built over them, so a quoted alias
    (`SELECT amount AS "<b>"`) reaches the header as a tag too and vanishes
    from it just the same, leaving a nameless column over cells that do show.

    The fence is one backtick longer than the longest run of backticks in the
    text, so a name containing one stays inside its span. Text that begins or
    ends with a backtick is padded a space each side -- which the renderer
    strips back off -- because otherwise the fence and the text run together
    into a longer fence and the span never opens.
    """
    escaped = _escaped(text)
    if not escaped:
        # An empty name has nothing to protect and no span to hold it: ``
        # reads as a two-backtick fence with no closer, so it would render as
        # two literal backticks where the honest rendering is an empty cell.
        return ""
    longest = max((len(run) for run in _BACKTICK_RUN.findall(escaped)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if escaped.startswith("`") or escaped.endswith("`") else ""
    return f"{fence}{pad}{escaped}{pad}{fence}"


def _example_row(label: str, cells: tuple[str, ...]) -> str:
    """One table row, indented to sit inside the Decision's list item."""
    return "    | " + " | ".join([label, *cells]) + " |"


def _render_example(example: Example) -> list[str]:
    """The worked example as a single Markdown table with two labelled row
    blocks, preceded by the sentence that says what the values are not.

    The label column carries the label on the first row of a block and is
    blank on the rest, which is the table convention for "same as above" --
    repeating "after this model runs" three times says nothing more.
    """
    # "Worked example." is this renderer's caption, and the only string on
    # this page that is. The sentence after it and the two row-block labels
    # below are claims -- about where these values came from, and about what
    # acted on them -- and `example.py` owns all three, because
    # `worked_example` is the only thing that can vouch for any of them.
    lines = [f"  - Worked example. {PLACEHOLDER_NOTICE}"]
    if example.key:
        lines.append(f"    Rows are matched on {example.key}.")
    lines.extend(
        [
            "",
            _example_row("", tuple(map(_example_cell, example.columns))),
            _example_row("---", ("---",) * len(example.columns)),
        ]
    )
    for i, row in enumerate(example.before):
        lines.append(
            _example_row(SUPPOSED_ROW_LABEL if i == 0 else "", tuple(map(_example_cell, row)))
        )
    for i, row in enumerate(example.model_after):
        lines.append(
            _example_row(AFTER_RUN_LABEL if i == 0 else "", tuple(map(_example_cell, row)))
        )
    lines.append("")
    return lines


def _example_for(decision: Decision, change: ProjectChange) -> Example | None:
    """The worked example for `decision`, drawn against the model built from
    the statement it came from, or None when there is no such model.

    The pairing is statement index against `AssembledModel.source_indices`,
    which is the match `assemble._find_incremental_decision_index` makes in
    the other direction and for the reason recorded there: a name collision
    gives two statements one model name, so `Subject.table` against
    `model.name` cannot tell two Decisions apart -- and `Subject.table` is
    the script's spelling of the target, while `model.name` is the renamed
    model, so the two do not even agree in the ordinary case.

    `worked_example` refuses a mismatched pair itself, but it should never be
    handed one: a caller that guesses at the model is asking for a confident
    picture of the wrong table.
    """
    index = statement_index(decision)
    if index is None:
        return None
    for model in change.models:
        if index in model.source_indices:
            return worked_example(decision, model, change.dialect)
    return None


def _render_decisions(change: ProjectChange) -> str:
    lines = ["## Decisions", ""]
    if not change.decisions:
        lines.append("No decisions were recorded.")
        return "\n".join(lines)

    by_tier: dict[int, list[Decision]] = {}
    for d in change.decisions:
        by_tier.setdefault(d.tier, []).append(d)

    tier_blocks: list[str] = []
    for tier in sorted(by_tier):
        block_lines = [f"### Tier {tier}", ""]
        for d in by_tier[tier]:
            location = f" ({d.source_file}:{d.line_start})" if d.source_file else ""
            block_lines.append(f"- **{d.action}** — {d.reason}{location}")
            # Under the dbt-native reason it restates, never instead of it,
            # and with the same "In plain words:" opening the plain question
            # and each option's plain wording already use. That repeated
            # opening is how a reader tells the two registers apart -- a
            # heading naming the audience ("for beginners") would sort the
            # readers instead of the sentences, and the dbt-native reader has
            # as much use for the plain one as the other way round.
            #
            # Outside the `if d.question` branch below, because the Decision
            # this exists for asks nothing: a rename is Tier 1 and carries no
            # question at all. Rendering it inside would have left the whole
            # cutover invisible.
            if d.plain_reason:
                block_lines.append(f"  - In plain words: {d.plain_reason}")
            if d.question:
                block_lines.append(f"  - Question: {d.question}  Chose: {d.chosen}")
                # The plain register, beside the dbt one rather than instead
                # of it -- two readers, two wordings, both engine-owned so
                # the report and a later screen cannot explain the same
                # choice differently. Each restatement is its own list item
                # one level under what it restates: Markdown folds
                # consecutive lines in a list item into one paragraph, so an
                # unbulleted continuation would run "Chose: append every row"
                # straight into the plain question as a single sentence.
                # One level deeper than the plain reason above, because it
                # restates the Question line rather than the Decision's own
                # bullet. Both opened at the same indent while a Decision
                # could only carry one of them; a Decision carrying both --
                # which nothing builds today and the caveats scheduled for a
                # plain register will -- printed two identical labels as
                # siblings, with nothing on the page saying which restated
                # what. The indents now run 2/4/6 for reason, question, and
                # option, matching what each one is under.
                if d.plain_question:
                    block_lines.append(f"    - In plain words: {d.plain_question}")
                # Every option, chosen one included, with the effect the
                # engine wrote for it. A reader deciding whether to change
                # the answer needs to know what the other one would do, and
                # the record already says -- rendering only the labels left
                # the report explaining the choice less than the screen
                # beside it, from the same Decision.
                if d.options:
                    block_lines.append("  - Answers:")
                    for option in d.options:
                        taken = " (chosen)" if option.label == d.chosen else ""
                        block_lines.append(f"    - {option.label}{taken} — {option.effect}")
                        if option.plain:
                            block_lines.append(f"      - In plain words: {option.plain}")
                example = _example_for(d, change)
                if example is not None:
                    block_lines.extend(_render_example(example))
        # The example ends on a blank line so the table it closes is not run
        # into the next bullet. When it is the last thing in the tier, that
        # blank would meet the one `render_report` puts between sections and
        # leave a two-line gap under the last Decision.
        while block_lines and not block_lines[-1]:
            block_lines.pop()
        tier_blocks.append("\n".join(block_lines))
    lines.append("\n\n".join(tier_blocks))
    return "\n".join(lines)


def _render_pending(change: ProjectChange) -> str:
    lines = ["## Still pending", ""]
    if not change.pending:
        lines.append("Nothing — every statement was handled.")
        return "\n".join(lines)
    for _, stmt in change.pending:
        first_line = stmt.raw.text.splitlines()[0] if stmt.raw.text else ""
        lines.append(f"- **{stmt.kind}** — {first_line}")
    return "\n".join(lines)
