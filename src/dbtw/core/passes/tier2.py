"""Tier-2 (decision-requiring) passes: conversions where the SQL alone
doesn't settle dbt's answer, so each conversion carries a design Decision
(or, when the SQL can't be mapped at all, a Decision explaining the refusal).

Like tier 1, each pass is a pure function PassState -> PassState. Consumed
statements always leave a Decision; refusals this pass is responsible for
(not simply "not this pass's statement shape") leave one too.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from dbtw.core.ingest.types import ClassifiedStatement
from dbtw.core.naming import qualified_name
from dbtw.core.passes.types import Decision, ModelDraft, PassState, Tier


def _parse(stmt: ClassifiedStatement, dialect: str | None) -> exp.Expr:
    return sqlglot.parse_one(stmt.raw.text, read=dialect)


def _as_table(obj: object) -> exp.Table | None:
    if isinstance(obj, exp.Table):
        return obj
    if isinstance(obj, exp.Schema) and isinstance(obj.this, exp.Table):
        return obj.this
    return None


def _target_of(node: exp.Expr) -> exp.Table | None:
    if isinstance(node, exp.Insert):
        return _as_table(node.this)
    if isinstance(node, exp.TruncateTable):
        return _as_table(node.expressions[0]) if node.expressions else None
    return None


def _decision(
    stmt: ClassifiedStatement, index: int, name: str, tier: Tier, action: str, reason: str
) -> Decision:
    return Decision(
        key=f"tier2.{name}.{stmt.raw.source_file}:{index}",
        tier=tier,
        action=action,
        reason=reason,
        source_file=stmt.raw.source_file,
        line_start=stmt.raw.line_start,
        line_end=stmt.raw.line_end,
    )


def truncate_insert_columns_pass(state: PassState) -> PassState:
    """Pair a pending TRUNCATE with a later column-list INSERT on the same
    target, mapping the INSERT's column list positionally onto its SELECT.

    This is the pairing tier 1's `truncate_insert_pass` deliberately refuses:
    an INSERT with an explicit column list (`node.this` is `exp.Schema`). An
    INSERT with no column list isn't this pass's concern at all — it's
    skipped exactly like a statement of the wrong kind, no Decision, because
    tier 1 already owns that pairing.

    A truncate is looked up by (source_file, qualified_name) — never by bare
    name, so two same-named tables in different schemas or catalogs can
    never cross-pair. Once a later matching INSERT is found, the truncate's
    lookup entry is deleted immediately, in the one place a match is made,
    *before* branching into "pairs cleanly" / "column count mismatch" /
    "star projection" outcomes. That single, unconditional deletion is what
    keeps slice 3's stale-entry bug from recurring: there is exactly one
    code path that can claim a truncate, so a second INSERT on the same
    target — however this first one is resolved — can never silently
    re-pair with an already-claimed truncate and discard prior work.
    """
    pending = list(state.pending)
    drafts = state.drafts
    decisions = list(state.decisions)
    consumed: set[int] = set()
    truncates: dict[tuple[str, str], tuple[int, ClassifiedStatement]] = {}
    for index, stmt in pending:
        if stmt.kind == "truncate":
            node = _parse(stmt, state.dialect)
            table = _target_of(node)
            if table is not None:
                key = (stmt.raw.source_file, qualified_name(table))
                truncates[key] = (index, stmt)
            continue
        if stmt.kind != "insert_select":
            continue
        node = _parse(stmt, state.dialect)
        if not isinstance(node.this, exp.Schema):
            continue  # no column list: tier 1's bare pair, not this pass's concern
        table = _target_of(node)
        if table is None:
            continue
        key = (stmt.raw.source_file, qualified_name(table))
        pair = truncates.get(key)
        if pair is None or pair[0] > index:
            continue
        # This is the only place a truncate is matched against a candidate
        # INSERT. Removing it here, before any branch below, means a truncate
        # can be claimed at most once — see the docstring.
        del truncates[key]
        columns = node.this.expressions
        select = node.expression
        projections = select.expressions if isinstance(select, exp.Select) else []
        if any(projection.is_star for projection in projections):
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "truncate_insert_columns",
                    2,
                    action=(
                        f"deferred: INSERT INTO {table.name} selects * against an explicit "
                        "column list — cannot map columns positionally"
                    ),
                    reason=(
                        "a star projection hides the column count, so the column list can't "
                        "be mapped onto it positionally"
                    ),
                )
            )
            continue
        if len(projections) != len(columns):
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "truncate_insert_columns",
                    2,
                    action=(
                        f"deferred: INSERT INTO {table.name} column count doesn't match its "
                        "SELECT's projection count"
                    ),
                    reason=(
                        "a positional column mapping needs the same number of columns and "
                        "projections on both sides"
                    ),
                )
            )
            continue
        aliased_select = select.copy()
        aliased_select.set(
            "expressions",
            [
                exp.alias_(projection.copy(), column.name)
                for projection, column in zip(projections, columns, strict=True)
            ],
        )
        body = aliased_select.sql(dialect=state.dialect, pretty=True)
        draft = ModelDraft(
            name=table.name,
            qualified_name=qualified_name(table),
            body=body,
            materialization="table",
            grants=(),
            source_indices=(pair[0], index),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
        )
        drafts = (*drafts, draft)
        consumed.update({pair[0], index})
        decisions.append(
            _decision(
                stmt,
                index,
                "truncate_insert_columns",
                1,
                action=(
                    f"TRUNCATE + INSERT INTO {table.name} became one model "
                    "(materialized='table'); its column list became positional SELECT aliases"
                ),
                reason=(
                    "truncate-then-insert is a full rebuild, like dbt's table materialization; "
                    "the column list maps positionally onto the SELECT's projections"
                ),
            )
        )
    return PassState(
        pending=tuple((i, s) for i, s in pending if i not in consumed),
        drafts=drafts,
        decisions=tuple(decisions),
        dialect=state.dialect,
    )
