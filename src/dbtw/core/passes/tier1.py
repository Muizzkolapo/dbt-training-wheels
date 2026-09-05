"""Tier-1 (mechanical) passes: certain conversions, applied without asking.

Each pass is a pure function PassState -> PassState. Consumed statements
always leave a Decision; unhandled statements stay pending for later tiers.
"""

from __future__ import annotations

import dataclasses

import sqlglot
from sqlglot import exp

from dbtw.core.ingest.types import ClassifiedStatement
from dbtw.core.naming import compare_targets, qualified_name, target_key
from dbtw.core.passes.collisions import replace_draft
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
            qualified_name=qualified_name(table),
            identity=target_key(table),
            body=body,
            materialization=materialization,
            grants=(),
            source_indices=(index,),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
        )
        drafts, verdict, existing = replace_draft(drafts, draft)
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
                        "a later statement in this conversion fully rebuilds this table; dbt keeps "
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
    # Keyed by naming.target_key, not by the target's raw spelling: unquoted
    # identifiers fold, so `TRUNCATE TABLE Rebuild_t` pairs with `INSERT INTO
    # rebuild_t`. A key miss means "no confirmed pair", never "a different
    # table" — a target qualified to a different degree is ambiguous, and
    # append_pass refuses to convert an INSERT with an ambiguous truncate
    # rather than treat the miss as licence.
    truncates: dict[tuple[str, tuple[str, str, str]], tuple[int, ClassifiedStatement]] = {}
    for index, stmt in pending:
        if stmt.kind == "truncate":
            node = _parse(stmt, state.dialect)
            table = _target_of(node)
            if table is not None:
                key = (stmt.raw.source_file, target_key(table))
                truncates[key] = (index, stmt)
            continue
        if stmt.kind != "insert_select":
            continue
        node = _parse(stmt, state.dialect)
        table = _target_of(node)
        if table is None:
            continue
        key = (stmt.raw.source_file, target_key(table))
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
            qualified_name=qualified_name(table),
            identity=target_key(table),
            body=body,
            materialization="table",
            grants=(),
            source_indices=(pair[0], index),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
        )
        drafts, verdict, existing = replace_draft(drafts, draft)
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
        # Matched on naming.target_key's folded name, not on raw text: a
        # GRANT spelling its table `orders` names the model `CREATE TABLE
        # Orders` just built, and dropping it as "an object this conversion
        # doesn't create" contradicts the model file written in the same run.
        grant_identity = None if table is None else target_key(table)
        match = next(
            (
                i
                for i, d in enumerate(drafts)
                if grant_identity is not None and d.identity[2] == grant_identity[2]
            ),
            None,
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
        drafts[match] = dataclasses.replace(d, grants=new_grants)
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
    inserts: list[tuple[str, exp.Table]] = []
    for _, stmt in state.pending:
        if stmt.kind != "insert_select":
            continue
        table = _target_of(_parse(stmt, state.dialect))
        if table is not None:
            inserts.append((stmt.raw.source_file, table))
    for index, stmt in state.pending:
        if stmt.kind == "truncate":
            table = _target_of(_parse(stmt, state.dialect))
            paired = table is not None and any(
                compare_targets(insert_table, table) in ("same", "ambiguous")
                for insert_file, insert_table in inserts
                if insert_file == stmt.raw.source_file
            )
            if paired:
                # An INSERT against this target is still pending, so a pass
                # that could have paired them declined to (a column list it
                # could not map, or a qualification it could not confirm).
                # Both halves stay pending together: dropping this one with
                # "no surviving INSERT pair" would contradict the deferral
                # Decision printed beside it in the same report.
                pending.append((index, stmt))
                continue
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
