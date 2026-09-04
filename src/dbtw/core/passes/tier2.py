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
    stmt: ClassifiedStatement,
    index: int,
    name: str,
    tier: Tier,
    action: str,
    reason: str,
    question: str = "",
    chosen: str = "",
    alternatives: tuple[str, ...] = (),
) -> Decision:
    return Decision(
        key=f"tier2.{name}.{stmt.raw.source_file}:{index}",
        tier=tier,
        action=action,
        reason=reason,
        source_file=stmt.raw.source_file,
        line_start=stmt.raw.line_start,
        line_end=stmt.raw.line_end,
        question=question,
        chosen=chosen,
        alternatives=alternatives,
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


def _merge_unique_key(node: exp.Merge, target: str) -> tuple[tuple[str, ...], str | None]:
    """Target-side column names from the ON clause; `(keys, refusal_reason)`.

    `refusal_reason` is `None` iff `keys` is non-empty; otherwise it names why
    no key could be extracted, for `merge_pass` to turn into a Decision:

    - `"disjunctive"`: the ON clause contains an `exp.Or` anywhere — dbt's
      `unique_key` is inherently a conjunction of columns, so a disjunctive
      match condition (`t.id = s.id OR t.legacy_id = s.legacy_id`) has no
      `unique_key` representation. Detected before any equality is walked,
      so an OR anywhere in the clause refuses the whole clause rather than
      silently keying on whichever equalities happen not to sit under it.
    - `"no_key"`: walking `find_all(exp.EQ)` (which descends through `And`
      transparently, so `t.id = s.id` and `t.a = s.a AND t.b = s.b` both
      yield their keys) found no usable equality. An equality is usable only
      when both sides are `exp.Column` (`ON 1 = 1` has neither) AND exactly
      one side's table qualifier matches `target` (the MERGE target's alias,
      or its bare name when unaliased) — `s.src_id = t.id` keys on `id`, not
      `src_id`, because `unique_key` must name a column on the target model,
      never the source. An equality qualified to neither side of the target
      (`other.x = elsewhere.y`) or ambiguously to both is excluded, same as
      one with a non-Column side. A target column equated twice (`t.id =
      s.id AND t.id = s.id2`) dedupes to one entry, in the order the
      equalities were written.
    """
    on = node.args["on"]
    if next(on.find_all(exp.Or), None) is not None:
        return (), "disjunctive"
    keys: list[str] = []
    for eq in on.find_all(exp.EQ):
        if not (isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)):
            continue
        this_is_target = eq.this.table == target
        expression_is_target = eq.expression.table == target
        if this_is_target and not expression_is_target:
            key_column = eq.this
        elif expression_is_target and not this_is_target:
            key_column = eq.expression
        else:
            continue  # ambiguous: neither side (or both sides) qualify to the target
        if key_column.name not in keys:
            keys.append(key_column.name)
    if not keys:
        return (), "no_key"
    return tuple(keys), None


def _merge_body(node: exp.Merge, dialect: str | None) -> str:
    """The MERGE's USING source, rendered as a standalone SELECT.

    A subquery USING (`USING (SELECT ...) AS s`) already *is* a query — its
    inner query is used directly. A plain table USING (`USING stg_c AS s`)
    isn't a query at all, so it's wrapped as `SELECT * FROM <table>`.
    """
    using = node.args["using"]
    if isinstance(using, exp.Subquery):
        query = using.this
    else:
        query = exp.select("*").from_(using.copy())
    return query.sql(dialect=dialect, pretty=True)


def merge_pass(state: PassState) -> PassState:
    """Convert a pending MERGE into an incremental model keyed on its ON clause.

    A MERGE's matched/not-matched branches are exactly what dbt's `merge`
    incremental strategy performs against `unique_key` — so the ON clause's
    equality columns become the draft's `unique_key`, and the USING source
    becomes the model body (see `_merge_body`). Whether that key actually
    identifies a row uniquely isn't decidable from the SQL alone (a MERGE
    still runs correctly even if `ON` picks out more than one existing row,
    just non-deterministically), so this is a tier-2 Decision: the user is
    asked to confirm the key, with "append every row" offered as the
    alternative to keeping the match/update semantics at all.

    A MERGE whose ON clause yields no extractable key can't be mapped to
    `unique_key` at all — dbt's merge strategy requires one. Two shapes
    refuse: no target-column = source-column equality at all (e.g. `ON 1 =
    1`, or every equality qualified to neither side of the target), and a
    disjunctive (OR-joined) ON clause, which has no `unique_key`
    representation regardless of what its equalities look like — see
    `_merge_unique_key`. Either way the MERGE is left pending, with a
    tier-2 Decision recording the refusal so it's never silently dropped.
    """
    pending = list(state.pending)
    drafts = state.drafts
    decisions = list(state.decisions)
    consumed: set[int] = set()
    for index, stmt in pending:
        if stmt.kind != "merge":
            continue
        node = _parse(stmt, state.dialect)
        assert isinstance(node, exp.Merge)  # classifier only assigns kind="merge" to this shape
        table = node.this
        assert isinstance(table, exp.Table)  # sqlglot's MERGE grammar always parses `this` as one
        target = table.alias or table.name
        keys, refusal_reason = _merge_unique_key(node, target)
        if refusal_reason is not None:
            if refusal_reason == "disjunctive":
                action = (
                    f"deferred: MERGE INTO {table.name} has no unique key extractable "
                    "from its ON clause — the ON clause is disjunctive (OR), which has "
                    "no unique_key representation"
                )
                reason = (
                    "dbt's merge incremental strategy's unique_key is inherently a "
                    "conjunction of columns; a disjunctive (OR) match condition cannot "
                    "be expressed as one, and converting it anyway would silently "
                    "change the MERGE's match semantics"
                )
            else:
                action = (
                    f"deferred: MERGE INTO {table.name} has no unique key extractable "
                    "from its ON clause"
                )
                reason = (
                    "dbt's merge incremental strategy requires a unique_key; an ON "
                    "clause with no target-column = source-column equality gives no "
                    "column to use as one"
                )
            decisions.append(_decision(stmt, index, "merge", 2, action=action, reason=reason))
            continue
        body = _merge_body(node, state.dialect)
        draft = ModelDraft(
            name=table.name,
            qualified_name=qualified_name(table),
            body=body,
            materialization="incremental",
            grants=(),
            source_indices=(index,),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
            incremental_strategy="merge",
            unique_key=keys,
        )
        drafts = (*drafts, draft)
        consumed.add(index)
        key_list = ", ".join(keys)
        decisions.append(
            _decision(
                stmt,
                index,
                "merge",
                2,
                action=(
                    f"MERGE INTO {table.name} became an incremental model "
                    f"(incremental_strategy='merge', unique_key={list(keys)!r})"
                ),
                reason=(
                    "MERGE's matched/not-matched branches are what dbt's merge "
                    "incremental strategy performs against unique_key"
                ),
                question=f"does {key_list} uniquely identify a row in {table.name}?",
                chosen=f"merge on {key_list}",
                alternatives=("append every row",),
            )
        )
    return PassState(
        pending=tuple((i, s) for i, s in pending if i not in consumed),
        drafts=drafts,
        decisions=tuple(decisions),
        dialect=state.dialect,
    )
