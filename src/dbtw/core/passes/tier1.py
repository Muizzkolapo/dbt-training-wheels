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


def _qualified(table: exp.Table) -> str:
    """Dotted catalog.db.name, dropping empty parts; bare name if unqualified."""
    return ".".join(part for part in (table.catalog, table.db, table.name) if part)


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


def _replace_draft(
    drafts: tuple[ModelDraft, ...], new: ModelDraft
) -> tuple[tuple[ModelDraft, ...], str | None, ModelDraft | None]:
    """Upsert `new` keyed by unqualified name, honestly resolving collisions.

    Compares file order by the highest source index each draft folds in, so
    whichever definition is later in the file always wins — regardless of
    which pass or which call built it first.

    Returns (drafts, verdict, existing):
    - verdict is None when there was no prior draft for this name.
    - "redefinition": same qualified name defined twice; later statement wins.
    - "collision": different qualified names map to the same model name; later wins.
    - "superseded": the existing draft is later in file order; `new` is dropped
      and `drafts` is returned unchanged.
    - `existing` is the prior draft when one was found, else None.
    """
    existing = next((d for d in drafts if d.name == new.name), None)
    if existing is None:
        return (*drafts, new), None, None
    if max(existing.source_indices) > max(new.source_indices):
        return drafts, "superseded", existing
    kept = tuple(d for d in drafts if d.name != new.name)
    verdict = "redefinition" if existing.qualified_name == new.qualified_name else "collision"
    return (*kept, new), verdict, existing


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
            qualified_name=_qualified(table),
            body=body,
            materialization=materialization,
            grants=(),
            source_indices=(index,),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
        )
        drafts, verdict, existing = _replace_draft(drafts, draft)
        if verdict == "superseded":
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "build.supersede",
                    action=(
                        f"superseded: this earlier definition of {table.name} is replaced "
                        "by a later full rebuild"
                    ),
                    reason=(
                        "a later statement in this file fully rebuilds this table; dbt keeps "
                        "only the final definition"
                    ),
                )
            )
            continue
        if verdict == "redefinition":
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "build.redefinition",
                    action=f"redefinition of {table.name} — kept the last definition",
                    reason="defined twice; a dbt model is one file, so the last definition wins",
                )
            )
        elif verdict == "collision":
            assert existing is not None  # a collision always has a prior draft
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "build.collision",
                    action=(
                        f"{existing.qualified_name} and {draft.qualified_name} both map to "
                        f"model {table.name} — kept {draft.qualified_name}; resolve the "
                        "collision before deploying"
                    ),
                    reason=(
                        "two different source tables produce the same model name; dbt needs "
                        "one definition per model"
                    ),
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
    truncates: dict[tuple[str, str, str, str], tuple[int, ClassifiedStatement]] = {}
    for index, stmt in pending:
        if stmt.kind == "truncate":
            node = _parse(stmt, state.dialect)
            table = _target_of(node)
            if table is not None:
                key = (stmt.raw.source_file, table.catalog, table.db, table.name)
                truncates[key] = (index, stmt)
            continue
        if stmt.kind != "insert_select":
            continue
        node = _parse(stmt, state.dialect)
        table = _target_of(node)
        if table is None:
            continue
        key = (stmt.raw.source_file, table.catalog, table.db, table.name)
        pair = truncates.get(key)
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
            del truncates[key]
            continue
        body = node.expression.sql(dialect=state.dialect, pretty=True)
        draft = ModelDraft(
            name=table.name,
            qualified_name=_qualified(table),
            body=body,
            materialization="table",
            grants=(),
            source_indices=(pair[0], index),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
        )
        drafts, verdict, existing = _replace_draft(drafts, draft)
        # Statements within a single call are processed in ascending file
        # order, and each table's truncates entry is deleted once paired, so
        # a later pair here can never be superseded by an earlier one.
        assert verdict != "superseded", "unreachable: pairs form in ascending file order"
        consumed.update({pair[0], index})
        del truncates[key]
        if verdict == "redefinition":
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "truncate_insert.redefinition",
                    action=f"redefinition of {table.name} — kept the last definition",
                    reason="defined twice; a dbt model is one file, so the last definition wins",
                )
            )
        elif verdict == "collision":
            assert existing is not None  # a collision always has a prior draft
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "truncate_insert.collision",
                    action=(
                        f"{existing.qualified_name} and {draft.qualified_name} both map to "
                        f"model {table.name} — kept {draft.qualified_name}; resolve the "
                        "collision before deploying"
                    ),
                    reason=(
                        "two different source tables produce the same model name; dbt needs "
                        "one definition per model"
                    ),
                )
            )
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
            qualified_name=d.qualified_name,
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
        if stmt.kind == "truncate":
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "ddl",
                    action=f"dropped solo TRUNCATE: {stmt.raw.text.splitlines()[-1][:60]}",
                    reason=(
                        "a TRUNCATE with no surviving INSERT pair has no dbt equivalent; dbt's "
                        "table materialization rebuilds from scratch on every run"
                    ),
                )
            )
            continue
        if stmt.kind != "ddl_other":
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
