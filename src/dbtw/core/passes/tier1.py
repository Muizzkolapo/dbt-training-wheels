"""Tier-1 (mechanical) passes: certain conversions, applied without asking.

Each pass is a pure function PassState -> PassState. Consumed statements
always leave a Decision; unhandled statements stay pending for later tiers.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from dbtw.core.ingest.types import ClassifiedStatement
from dbtw.core.passes.types import Decision, ModelDraft, PassState


def _parse(stmt: ClassifiedStatement, dialect: str | None) -> exp.Expr:
    return sqlglot.parse_one(stmt.raw.text, read=dialect)


def _as_table(obj: object) -> exp.Table | None:
    if isinstance(obj, exp.Table):
        return obj
    if isinstance(obj, exp.Schema) and isinstance(obj.this, exp.Table):
        return obj.this
    return None


def _target_of(node: exp.Expr) -> exp.Table | None:
    if isinstance(node, exp.Create | exp.Insert):
        return _as_table(node.this)
    if isinstance(node, exp.TruncateTable):
        return _as_table(node.expressions[0]) if node.expressions else None
    if isinstance(node, exp.Select):
        into = node.args.get("into")
        return _as_table(into.this) if into is not None else None
    if isinstance(node, exp.Grant):
        return _as_table(node.args.get("securable"))
    return None


def _decision(
    stmt: ClassifiedStatement, index: int, name: str, action: str, reason: str
) -> Decision:
    return Decision(
        key=f"tier1.{name}.{stmt.raw.source_file}:{index}",
        tier=1,
        action=action,
        reason=reason,
        source_file=stmt.raw.source_file,
        line_start=stmt.raw.line_start,
        line_end=stmt.raw.line_end,
    )


def _upsert_draft(
    drafts: tuple[ModelDraft, ...], new: ModelDraft
) -> tuple[tuple[ModelDraft, ...], bool]:
    """Insert new draft; replace an existing same-named one. Returns (drafts, replaced)."""
    kept = tuple(d for d in drafts if d.name != new.name)
    return (*kept, new), len(kept) != len(drafts)


def build_models_pass(state: PassState) -> PassState:
    pending: list[tuple[int, ClassifiedStatement]] = []
    drafts = state.drafts
    decisions = list(state.decisions)
    for index, stmt in state.pending:
        if stmt.kind not in ("create_table_as", "create_view"):
            pending.append((index, stmt))
            continue
        node = _parse(stmt, state.dialect)
        table = _target_of(node)
        if table is None:
            pending.append((index, stmt))
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "build",
                    action="left as-is: couldn't determine the target table name",
                    reason="a model needs a name; the target isn't a plain table reference",
                )
            )
            continue
        if isinstance(node, exp.Select):
            body_node = node.copy()
            body_node.set("into", None)
            body = body_node.sql(dialect=state.dialect, pretty=True)
        else:
            body = node.expression.sql(dialect=state.dialect, pretty=True)
        materialization = "view" if stmt.kind == "create_view" else "table"
        draft = ModelDraft(
            name=table.name,
            body=body,
            materialization=materialization,
            grants=(),
            source_indices=(index,),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
        )
        drafts, replaced = _upsert_draft(drafts, draft)
        if replaced:
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "build",
                    action=f"redefinition of {table.name} — kept the last definition",
                    reason="defined twice; a dbt model is one file, so the last definition wins",
                )
            )
        decisions.append(
            _decision(
                stmt,
                index,
                "build",
                action=f"created model {table.name} (materialized='{materialization}')",
                reason="dbt models are SELECTs; the CREATE wrapper becomes materialization config",
            )
        )
    return PassState(
        pending=tuple(pending), drafts=drafts, decisions=tuple(decisions), dialect=state.dialect
    )


def truncate_insert_pass(state: PassState) -> PassState:
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
                truncates[(stmt.raw.source_file, table.name)] = (index, stmt)
            continue
        if stmt.kind != "insert_select":
            continue
        node = _parse(stmt, state.dialect)
        table = _target_of(node)
        if table is None:
            continue
        pair = truncates.get((stmt.raw.source_file, table.name))
        if pair is None or pair[0] > index:
            continue
        if isinstance(node.this, exp.Schema):
            decisions.append(
                Decision(
                    key=f"tier1.truncate_insert.{stmt.raw.source_file}:{index}",
                    tier=2,
                    action=(
                        f"deferred: TRUNCATE+INSERT into {table.name} has an explicit column list"
                    ),
                    reason=("column-to-column mapping is a Tier 2 decision; left for that pass"),
                    source_file=stmt.raw.source_file,
                    line_start=stmt.raw.line_start,
                    line_end=stmt.raw.line_end,
                )
            )
            del truncates[(stmt.raw.source_file, table.name)]
            continue
        body = node.expression.sql(dialect=state.dialect, pretty=True)
        draft = ModelDraft(
            name=table.name,
            body=body,
            materialization="table",
            grants=(),
            source_indices=(pair[0], index),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
        )
        drafts, _ = _upsert_draft(drafts, draft)
        consumed.update({pair[0], index})
        del truncates[(stmt.raw.source_file, table.name)]
        decisions.append(
            _decision(
                stmt,
                index,
                "truncate_insert",
                action=(
                    f"TRUNCATE + INSERT INTO {table.name} became one model (materialized='table')"
                ),
                reason="truncate-then-insert is a full rebuild, like dbt's table materialization",
            )
        )
    return PassState(
        pending=tuple((i, s) for i, s in pending if i not in consumed),
        drafts=drafts,
        decisions=tuple(decisions),
        dialect=state.dialect,
    )


def grants_pass(state: PassState) -> PassState:
    pending: list[tuple[int, ClassifiedStatement]] = []
    drafts = list(state.drafts)
    decisions = list(state.decisions)
    for index, stmt in state.pending:
        if stmt.kind != "grant":
            pending.append((index, stmt))
            continue
        node = _parse(stmt, state.dialect)
        if isinstance(node, exp.Revoke):
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "grants",
                    action="dropped: REVOKE has no dbt equivalent",
                    reason=(
                        "dbt's grants config is declarative — each run applies exactly "
                        "the listed grants, so revocation is expressed by omission"
                    ),
                )
            )
            continue
        table = _target_of(node)
        privileges = tuple(p.sql(dialect=state.dialect) for p in node.args.get("privileges") or ())
        principals = tuple(p.sql(dialect=state.dialect) for p in node.args.get("principals") or ())
        match = next(
            (i for i, d in enumerate(drafts) if table is not None and d.name == table.name), None
        )
        if match is None:
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "grants",
                    action=(
                        "dropped with note: GRANT references an object "
                        "this conversion doesn't create"
                    ),
                    reason=(
                        "dbt grants attach to a model's config; there is "
                        "no model here to attach them to"
                    ),
                )
            )
            continue
        d = drafts[match]
        new_grants = d.grants + tuple((priv, principals) for priv in privileges)
        drafts[match] = ModelDraft(
            name=d.name,
            body=d.body,
            materialization=d.materialization,
            grants=new_grants,
            source_indices=d.source_indices,
            leading_comments=d.leading_comments,
        )
        decisions.append(
            _decision(
                stmt,
                index,
                "grants",
                action=f"attached grants to model {d.name}",
                reason=(
                    "GRANT statements become the model's grants config, "
                    "applied by dbt after each build"
                ),
            )
        )
    return PassState(
        pending=tuple(pending),
        drafts=tuple(drafts),
        decisions=tuple(decisions),
        dialect=state.dialect,
    )


def drop_session_pass(state: PassState) -> PassState:
    pending: list[tuple[int, ClassifiedStatement]] = []
    decisions = list(state.decisions)
    for index, stmt in state.pending:
        if stmt.kind != "session":
            pending.append((index, stmt))
            continue
        decisions.append(
            _decision(
                stmt,
                index,
                "session",
                action=f"dropped session statement: {stmt.raw.text.splitlines()[-1][:60]}",
                reason="connection and session state live in profiles.yml, not in models",
            )
        )
    return PassState(
        pending=tuple(pending),
        drafts=state.drafts,
        decisions=tuple(decisions),
        dialect=state.dialect,
    )


def drop_ddl_pass(state: PassState) -> PassState:
    pending: list[tuple[int, ClassifiedStatement]] = []
    decisions = list(state.decisions)
    for index, stmt in state.pending:
        if stmt.kind not in ("ddl_other", "truncate"):
            pending.append((index, stmt))
            continue
        decisions.append(
            _decision(
                stmt,
                index,
                "ddl",
                action=f"dropped DDL statement: {stmt.raw.text.splitlines()[-1][:60]}",
                reason=(
                    "dbt rebuilds objects from scratch; if an index is genuinely needed it "
                    "belongs in a post-hook"
                ),
            )
        )
    return PassState(
        pending=tuple(pending),
        drafts=state.drafts,
        decisions=tuple(decisions),
        dialect=state.dialect,
    )
