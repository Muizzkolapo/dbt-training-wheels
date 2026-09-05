"""Tier-2 (decision-requiring) passes: conversions where the SQL alone
doesn't settle dbt's answer, so each conversion carries a design Decision
(or, when the SQL can't be mapped at all, a Decision explaining the refusal).

Like tier 1, each pass is a pure function PassState -> PassState. Consumed
statements always leave a Decision; refusals this pass is responsible for
(not simply "not this pass's statement shape") leave one too.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from dbtw.core.ingest.types import ClassifiedStatement
from dbtw.core.naming import compare_targets, qualified_name, same_identifier
from dbtw.core.passes.collisions import replace_draft
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
    if isinstance(node, exp.Delete):
        return _as_table(node.this)
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


def _collision_decision(
    stmt: ClassifiedStatement,
    index: int,
    pass_name: str,
    tier: Tier,
    table_name: str,
    draft: ModelDraft,
    verdict: str,
    existing: ModelDraft,
) -> Decision:
    """Turn a `collisions.replace_draft` verdict into the Decision it implies.

    Mirrors tier 1's wording for the same three verdicts (`superseded`,
    `redefinition`, `collision`) so a model's collision history reads the
    same regardless of which tier resolved it -- see `collisions.py` for why
    every drafting pass, tier 1 and tier 2 alike, must route through
    `replace_draft` rather than appending a draft directly.
    """
    if verdict == "superseded":
        return _decision(
            stmt,
            index,
            f"{pass_name}.supersede",
            tier,
            action=(
                f"superseded: this earlier definition of {table_name} is replaced "
                "by a later definition"
            ),
            reason=(
                "a later statement in this file defines this model; dbt keeps "
                "only the final definition"
            ),
        )
    if verdict == "redefinition":
        return _decision(
            stmt,
            index,
            f"{pass_name}.redefinition",
            tier,
            action=f"redefinition of {table_name} — kept the last definition",
            reason="defined twice; a dbt model is one file, so the last definition wins",
        )
    return _decision(
        stmt,
        index,
        f"{pass_name}.collision",
        tier,
        action=(
            f"{existing.qualified_name} and {draft.qualified_name} both map to "
            f"model {table_name} — kept {draft.qualified_name}; resolve the "
            "collision before deploying"
        ),
        reason=(
            "two different source tables produce the same model name; dbt needs "
            "one definition per model"
        ),
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
        drafts, verdict, existing = replace_draft(drafts, draft)
        consumed.update({pair[0], index})
        if verdict is not None:
            assert existing is not None  # every non-None verdict has a prior draft
            decisions.append(
                _collision_decision(
                    stmt, index, "truncate_insert_columns", 1, table.name, draft, verdict, existing
                )
            )
        if verdict == "superseded":
            continue
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


@dataclass(frozen=True, slots=True)
class _MergeBranches:
    """What a MERGE's WHEN branches actually do — see `_merge_branches`."""

    unsupported: tuple[str, ...]  # branches dbt's merge cannot perform, verbatim
    unreadable: tuple[str, ...]  # branches assigning something other than a column, verbatim
    valueless_inserts: int  # INSERT branches naming columns but supplying no values
    updates_matched: bool  # any WHEN MATCHED THEN UPDATE branch at all
    inserts_unmatched: bool  # any WHEN NOT MATCHED THEN INSERT branch at all
    restricted_updates: int  # of those, how many named particular columns
    restricted_inserts: int  # of those, how many named particular columns
    updated_columns: tuple[str, ...]  # named UPDATE SET targets; () means "all"
    inserted_columns: tuple[str, ...]  # named INSERT target columns; () means "all"
    conditions: tuple[str, ...]  # per-branch conditions dbt's merge has no place for


def _named_columns(node: exp.Expr) -> list[tuple[str, bool]] | None:
    """The particular columns a branch target names, each with whether it was
    written quoted; `[]` when it names none, or `None` when it names them in a
    shape that is not a column at all.

    `[]` and `None` are different answers to different questions. `[]` says
    the target restricts nothing — every column, which is what dbt's merge
    does anyway and so needs no caveat. `None` says the target is not a
    column, so there is no restriction to report and no conversion to make;
    `_merge_branches` refuses the branch rather than guess which column it
    meant.

    The shapes, probed on sqlglot 30.18.0:

    - `exp.Star` — a bare `*`, as in `UPDATE SET *` and `INSERT *`: every
      column.
    - `exp.Column` — `t.n`, bare `n`, `t.a.b`, and each member of a Tuple. A
      `t.*` is a Column wrapping a Star, so it means every column too.
    - `exp.Tuple` — an INSERT list's `(id, city)`, and the target of a
      row-value assignment `SET (city, email) = (s.city, s.email)`, which is
      the standard's spelling of `SET city = s.city, email = s.email` and
      restricts the update to exactly the same columns.
    - `exp.Paren` — a row value naming exactly one column, `SET (city) =
      (s.city)`, which sqlglot does not make a one-member Tuple of. Same
      restriction, one bracket shallower.
    - anything else — `SET t.data['city'] = s.city` puts a `Bracket` here,
      assigning one path inside a column rather than the column. dbt's merge
      assigns whole columns only, so there is nothing to convert it into.
    """
    if isinstance(node, exp.Star):
        return []
    if isinstance(node, exp.Paren):
        return _named_columns(node.this)
    if isinstance(node, exp.Tuple):
        columns: list[tuple[str, bool]] = []
        for member in node.expressions:
            named = _named_columns(member)
            if named is None:
                return None
            columns.extend(named)
        return columns
    if not isinstance(node, exp.Column):
        return None
    identifier = node.this
    if isinstance(identifier, exp.Star):
        return []
    return [(node.name, bool(isinstance(identifier, exp.Identifier) and identifier.quoted))]


def _insert_without_values(then: exp.Insert) -> bool:
    """Whether an INSERT branch names target columns but supplies nothing to
    put in them, which is not a restriction to those columns at all.

    `INSERT DEFAULT VALUES` writes a row of the target's own column defaults
    and reads nothing from the source. sqlglot parses it into a target column
    list holding the DEFAULT keyword rather than a shape of its own (probed on
    30.18.0), so it arrives looking exactly like `INSERT (DEFAULT)` — read as
    a column list it would invent a column named DEFAULT that no target has.
    sqlglot also accepts `INSERT (id, city)` with no VALUES clause, which no
    warehouse would run; both are a column list with nothing to fill it.

    `INSERT *`, `INSERT VALUES (...)` and Snowflake's `INSERT ROW` each leave
    something other than a Tuple in `this` (probed), so none of them reach
    here and none is refused for it.
    """
    return isinstance(then.this, exp.Tuple) and not then.args.get("expression")


def _branch_columns(then: exp.Update | exp.Insert) -> list[tuple[str, bool]] | None:
    """The columns a branch's action restricts itself to, `[]` when it
    restricts itself to none, or `None` when one of its targets is not a
    column — see `_named_columns` for what those three answers mean.
    """
    if isinstance(then, exp.Insert):
        target = then.this
        # `INSERT VALUES ...` leaves `this` None, `INSERT *` a Star and
        # Snowflake's `INSERT ROW` a Var — none of which names a column
        # (probed on 30.18.0). `INSERT (id, city) VALUES ...` writes a Tuple
        # of them, which `_named_columns` reads.
        if target is None or isinstance(target, exp.Var):
            return []
        return _named_columns(target)
    columns: list[tuple[str, bool]] = []
    for assignment in then.args["expressions"]:
        target = assignment.this if isinstance(assignment, exp.EQ) else assignment
        named = _named_columns(target)
        if named is None:
            return None
        columns.extend(named)
    return columns


def _add_unique(columns: list[tuple[str, bool]], column: tuple[str, bool]) -> None:
    """Append `column` unless `columns` already names it.

    Deduped by `naming.same_identifier`, so two unquoted spellings fold
    case-insensitively (`t.CITY` after `t.city` adds nothing) while a quoted
    spelling never folds into an unquoted one — a quoted identifier's case is
    significant in every dialect that respects quoting.
    """
    name, quoted = column
    if not any(same_identifier(name, quoted, seen, seen_quoted) for seen, seen_quoted in columns):
        columns.append(column)


def _merge_branches(node: exp.Merge, dialect: str | None) -> _MergeBranches:
    """Read the MERGE's WHEN branches — the half of the statement that says
    what it does to a row, and the half `merge_pass` used to ignore entirely.

    dbt's merge incremental strategy performs exactly two actions, always:
    update a row matching `unique_key` with every column the model selects,
    and insert a row matching none. There is no third branch and no
    per-branch condition. So each WHEN branch is one of:

    - a matched `UPDATE` (`then` is `exp.Update`) — which dbt does too,
      except dbt updates *every* selected column, so a SET list naming
      particular columns is a restriction dbt will not honour and is
      collected into `updated_columns`.
    - an unmatched `INSERT` (`then` is `exp.Insert`) — which dbt does too,
      and again over every selected column, so an explicit target column
      list is the same kind of restriction and is collected into
      `inserted_columns`.
    - a branch whose target is not a column at all (`SET t.data['city'] =
      s.city`), collected verbatim into `unreadable`. What it assigns cannot
      be stated in dbt's terms — see `_named_columns`.
    - an INSERT that names columns but supplies no values (`INSERT DEFAULT
      VALUES`), counted into `valueless_inserts`. It is counted rather than
      quoted because sqlglot re-renders it as `INSERT (DEFAULT)`, and a
      refusal that quotes SQL the user never wrote is its own small lie —
      see `_insert_without_values`.
    - anything else, collected verbatim into `unsupported`. `THEN DELETE`
      and `THEN DO NOTHING` both parse to an `exp.Var` holding the source
      text as written (so lowercase SQL gives `Var(this='delete')` —
      probed), and a `NOT MATCHED BY SOURCE` branch sets `When.source`
      (probed), whatever its action.

    dbt's merge can perform neither of the last two, so `merge_pass` refuses
    a MERGE carrying one rather than emitting a model that silently drops it.
    They are kept apart because they are refused for different reasons, and a
    refusal that misstates its reason is no better than a silent one.

    `conditions` collects each surviving branch's `WHEN ... AND <cond>`
    and any `WHERE` sqlglot attached to its action, because dbt applies its
    two actions to every row regardless of them.

    A target column named more than once (`SET t.city = s.a, t.CITY = s.b`,
    or once in each of two matched branches) is reported once, in the order
    it was written — see `_add_unique`. `restricted_updates` and
    `restricted_inserts` count the branches that named any column at all, so
    a caveat can say how many branches it is describing. A branch assigning
    every column (`SET t.* = s.*`) restricts nothing and is not counted: it
    is what dbt's merge does anyway, and counting it would claim the columns
    named are all the statement ever assigns.
    """
    unsupported: list[str] = []
    unreadable: list[str] = []
    valueless_inserts = 0
    updates_matched = False
    inserts_unmatched = False
    restricted_updates = 0
    restricted_inserts = 0
    updated: list[tuple[str, bool]] = []
    inserted: list[tuple[str, bool]] = []
    conditions: list[str] = []
    for when in node.args["whens"].expressions:
        then = when.args["then"]
        if when.args["source"] or not isinstance(then, (exp.Update, exp.Insert)):
            unsupported.append(when.sql(dialect=dialect))
            continue
        if isinstance(then, exp.Insert) and _insert_without_values(then):
            valueless_inserts += 1
            continue
        named = _branch_columns(then)
        if named is None:
            unreadable.append(when.sql(dialect=dialect))
            continue
        condition = when.args["condition"]
        if condition is not None:
            conditions.append(condition.sql(dialect=dialect))
        where = then.args.get("where")
        if where is not None:
            conditions.append(where.this.sql(dialect=dialect))
        if isinstance(then, exp.Insert):
            inserts_unmatched = True
            collected = inserted
            if named:
                restricted_inserts += 1
        else:
            updates_matched = True
            collected = updated
            if named:
                restricted_updates += 1
        for column in named:
            _add_unique(collected, column)
    return _MergeBranches(
        unsupported=tuple(unsupported),
        unreadable=tuple(unreadable),
        valueless_inserts=valueless_inserts,
        updates_matched=updates_matched,
        inserts_unmatched=inserts_unmatched,
        restricted_updates=restricted_updates,
        restricted_inserts=restricted_inserts,
        updated_columns=tuple(name for name, _ in updated),
        inserted_columns=tuple(name for name, _ in inserted),
        conditions=tuple(conditions),
    )


def _restriction_phrase(count: int, table_name: str, clause: str, verb: str, named: str) -> str:
    """How many branches restrict themselves to which columns, worded so it
    stays true when the MERGE has other branches of the same kind.

    A MERGE can carry several matched branches and several unmatched ones, and
    only some of them need name columns. "dim_c's WHEN MATCHED branch assigns
    only city" is false twice over for a MERGE whose other matched branch
    assigns `t.* = s.*`: it is not *the* branch, and city is not all the
    statement assigns. The indefinite article and the count fix both.
    """
    if count == 1:
        return f"a {clause} branch of {table_name} {verb}s only {named}"
    return f"{count} {clause} branches of {table_name} {verb} only {named} between them"


def _merge_caveats(branches: _MergeBranches, table_name: str) -> list[tuple[str, str, str]]:
    """Every way dbt's merge strategy will act differently from the branches
    this MERGE actually wrote, as `(decision name, action, reason)` triples.

    These are caveats, not refusals: the conversion still happens (the model
    body is the USING source either way), and what changes is that its
    consequences are on the record instead of being discovered in
    production. Each names the difference in dbt's own terms so the reader
    can decide whether the converted model is still the job they meant.

    No `merge_update_columns` config is emitted for the restricted-SET case:
    adapter support for it varies and the adapter isn't known at convert
    time, so emitting it would be a guess. It is named in prose as the
    user's option instead.
    """
    caveats: list[tuple[str, str, str]] = []
    if not branches.inserts_unmatched:
        caveats.append(
            (
                "merge.no_insert_branch",
                f"caveat: {table_name} has no WHEN NOT MATCHED branch, but the converted "
                "model will insert rows that match nothing",
                "dbt's merge incremental strategy always inserts a row that matches no "
                "unique_key; this MERGE only acts on rows that already exist, so the "
                "converted model inserts rows the script never did",
            )
        )
    if not branches.updates_matched:
        caveats.append(
            (
                "merge.no_update_branch",
                f"caveat: {table_name} has no WHEN MATCHED THEN UPDATE branch, but the "
                "converted model will update every row matching its unique_key",
                "dbt's merge incremental strategy always updates a row matching unique_key "
                "with every column the model selects; this MERGE only inserts rows that "
                "match nothing, so the converted model overwrites rows the script left alone",
            )
        )
    if branches.updated_columns:
        named = ", ".join(branches.updated_columns)
        phrase = _restriction_phrase(
            branches.restricted_updates, table_name, "WHEN MATCHED", "assign", named
        )
        caveats.append(
            (
                "merge.update_columns",
                f"caveat: {phrase}, but the converted model updates every column it selects",
                "dbt's merge incremental strategy updates a matched row with every column "
                "the model selects, not a chosen subset, so the columns this MERGE leaves "
                "alone are overwritten on every run. The adapter-specific "
                "merge_update_columns config restores the restriction where the adapter "
                "supports it; it is not emitted here because the adapter is not known at "
                "convert time",
            )
        )
    if branches.inserted_columns:
        named = ", ".join(branches.inserted_columns)
        phrase = _restriction_phrase(
            branches.restricted_inserts, table_name, "WHEN NOT MATCHED", "insert", named
        )
        caveats.append(
            (
                "merge.insert_columns",
                f"caveat: {phrase}, but the converted model inserts every column it selects",
                "dbt's merge incremental strategy builds an inserted row from every column "
                "the model selects, not a chosen subset, so a column this MERGE's INSERT "
                "left to the target's default is written from the model's own SELECT instead",
            )
        )
    if branches.conditions:
        stated = "; ".join(branches.conditions)
        caveats.append(
            (
                "merge.branch_condition",
                f"caveat: {table_name} restricts a WHEN branch with a condition ({stated}), "
                "but the converted model applies dbt's merge to every row",
                "dbt's merge incremental strategy has no per-branch condition: every row "
                "matching unique_key is updated and every row matching none is inserted, "
                "whatever a branch condition says. The condition is named here as evidence "
                "for a human to re-express in the model's own SELECT, never applied as one",
            )
        )
    return caveats


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

    The ON clause's equality columns become the draft's `unique_key` (see
    `_merge_unique_key`) and the USING source becomes the model body (see
    `_merge_body`). Whether that key actually identifies a row uniquely
    isn't decidable from the SQL alone (a MERGE still runs correctly even if
    `ON` picks out more than one existing row, just non-deterministically),
    so this is a tier-2 Decision: the user is asked to confirm the key, with
    "append every row" offered as the alternative to keeping the
    match/update semantics at all.

    What the MERGE's WHEN branches do is a separate question from what its
    ON clause keys on, and `_merge_branches` answers it. dbt's merge
    strategy performs its own two fixed actions — update a matched row with
    every column the model selects, insert a row that matches nothing — so a
    MERGE is only equivalent to it when it writes exactly those two
    branches, unconditionally, over every column. Every other shape either
    refuses or converts with the difference on the record:

    - a branch dbt cannot perform at all (`THEN DELETE`, `THEN DO NOTHING`,
      `WHEN NOT MATCHED BY SOURCE`) refuses the whole statement, since
      converting would drop what that branch does with nothing written down;
    - a branch assigning something other than a whole column (`SET
      t.data['city'] = s.city`) refuses for its own reason, recorded
      separately: dbt assigns whole columns, so the branch is not
      convertible and naming the column it touches would overstate it;
    - an `INSERT DEFAULT VALUES` branch refuses for a third reason of its
      own: it takes nothing from the source, so dbt's insert-from-SELECT has
      nothing to stand in for it;
    - a missing branch, a SET or INSERT column list naming particular
      columns, or a branch condition converts, each with its own caveat
      Decision naming what dbt will do differently (see `_merge_caveats`).

    A MERGE whose ON clause yields no extractable key can't be mapped to
    `unique_key` at all — dbt's merge strategy requires one. Two shapes
    refuse: no target-column = source-column equality at all (e.g. `ON 1 =
    1`, or every equality qualified to neither side of the target), and a
    disjunctive (OR-joined) ON clause, which has no `unique_key`
    representation regardless of what its equalities look like — see
    `_merge_unique_key`.

    A refused MERGE is left pending with a tier-2 Decision recording the
    refusal so it's never silently dropped. A statement that trips both
    refusals gets both Decisions: one reason recorded is not a licence to
    leave the other silent.
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
        branches = _merge_branches(node, state.dialect)
        keys, refusal_reason = _merge_unique_key(node, target)
        refused = False
        if branches.unsupported:
            refused = True
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "merge.unsupported_branch",
                    2,
                    action=(
                        f"deferred: MERGE INTO {table.name} has a WHEN branch dbt's merge "
                        "incremental strategy cannot perform "
                        f"({'; '.join(branches.unsupported)})"
                    ),
                    reason=(
                        "dbt's merge incremental strategy performs exactly two actions: "
                        "update a row matching unique_key with every column the model "
                        "selects, and insert a row matching none. It has no delete branch, "
                        "no NOT MATCHED BY SOURCE branch and no way to leave a matched row "
                        "alone, so converting this MERGE would silently drop what that "
                        "branch does"
                    ),
                )
            )
        if branches.unreadable:
            refused = True
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "merge.unreadable_branch",
                    2,
                    action=(
                        f"deferred: MERGE INTO {table.name} has a WHEN branch that assigns "
                        "something other than a whole column "
                        f"({'; '.join(branches.unreadable)})"
                    ),
                    reason=(
                        "dbt's merge incremental strategy assigns whole columns, taken from "
                        "the model's own SELECT. This branch's target is not a whole column "
                        "— an assignment to a path inside one, like SET t.data['city'] = "
                        "s.city, is the usual case — so dbt has no way to perform it, and a "
                        "converted model would overwrite the whole column on every run"
                    ),
                )
            )
        if branches.valueless_inserts:
            refused = True
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "merge.valueless_insert_branch",
                    2,
                    action=(
                        f"deferred: MERGE INTO {table.name} has a WHEN NOT MATCHED branch "
                        "whose INSERT names target columns but supplies no values for them "
                        "(INSERT DEFAULT VALUES writes a row of the target's own column "
                        "defaults)"
                    ),
                    reason=(
                        "dbt's merge incremental strategy builds an inserted row from every "
                        "column the model selects. This branch takes nothing from the source "
                        "at all, so there is no source row for the model's SELECT to stand "
                        "in for, and a converted model would write selected rows where the "
                        "script wrote defaults"
                    ),
                )
            )
        if refusal_reason is not None:
            refused = True
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
        if refused:
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
        drafts, verdict, existing = replace_draft(drafts, draft)
        consumed.add(index)
        if verdict is not None:
            assert existing is not None  # every non-None verdict has a prior draft
            decisions.append(
                _collision_decision(stmt, index, "merge", 2, table.name, draft, verdict, existing)
            )
        if verdict == "superseded":
            continue
        key_list = ", ".join(keys)
        performed = " and ".join(
            phrase
            for phrase, present in (
                ("updates matched rows", branches.updates_matched),
                ("inserts unmatched rows", branches.inserts_unmatched),
            )
            if present
        )
        # Every branch is an update, an insert, or unsupported (which refused
        # above), and sqlglot's grammar requires at least one branch for a
        # MERGE to parse at all — so at least one phrase always survives.
        assert performed
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
                    "dbt's merge incremental strategy updates a row matching unique_key "
                    "with every column the model selects, and inserts a row matching "
                    f"none; this MERGE {performed}"
                ),
                question=f"does {key_list} uniquely identify a row in {table.name}?",
                chosen=f"merge on {key_list}",
                alternatives=("append every row",),
            )
        )
        for caveat_name, caveat_action, caveat_reason in _merge_caveats(branches, table.name):
            decisions.append(
                _decision(stmt, index, caveat_name, 2, action=caveat_action, reason=caveat_reason)
            )
    return PassState(
        pending=tuple((i, s) for i, s in pending if i not in consumed),
        drafts=drafts,
        decisions=tuple(decisions),
        dialect=state.dialect,
    )


def append_pass(state: PassState) -> PassState:
    """Convert a pending bare INSERT...SELECT (no column list) into an
    append incremental model.

    An INSERT with an explicit column list is Task 2's pairing target — this
    pass never touches one, exactly like it never touches an INSERT already
    consumed. What remains here, with no column list, has no truncate to
    pair with (else `truncate_insert_pass`/`truncate_insert_columns_pass`
    would already have claimed it) and no MERGE-style match/update logic —
    it just appends whatever its SELECT returns, which is precisely dbt's
    `incremental_strategy="append"`.

    Whether that's actually correct — whether the model's own SELECT already
    filters to new rows, or every run will re-insert everything it selects —
    isn't decidable from the SQL alone, so this is a tier-2 Decision offering
    "merge on a unique key" as the alternative. If the SELECT carries a
    WHERE, honesty forbids guessing that it's the incremental filter (a
    dbt `{% if is_incremental() %}` guard is never synthesized around it):
    the WHERE is named verbatim in the reason as evidence for a human to
    confirm, nothing more.

    A pending DELETE on the same target, in the same file, means this INSERT
    isn't a bare append at all — it's one half of a delete+insert pair
    (catalog 2.3), which only replaces the deleted rows' slice. That is not
    what an append incremental does: an append re-inserts everything the
    model selects on every run, keeping rows outside the deleted slice too.
    Converting it anyway would silently change which rows survive a run, so
    a confirmed-same target is left pending — deferred to slice 6c — with a
    tier-2 Decision recording the deferral instead of a draft.

    "Same target" is decided by `naming.compare_targets`, never a bare
    `qualified_name` string match: two spellings that only differ by case
    (`DELETE FROM Events` / `INSERT INTO events`) are still the same table,
    and — the failure mode that actually matters — two spellings that
    qualify their target to different *degrees* (`DELETE FROM db.events` /
    `INSERT INTO events`) are not safely "different" either, since whether
    the unqualified one resolves to the same object depends on the
    session's default schema, not on the SQL text. `compare_targets`'s
    `"ambiguous"` outcome for that case is treated exactly like a confirmed
    match here — deferred, never append-converted — because guessing wrong
    ships an incremental with silently different semantics from the
    script; only a confirmed `"different"` lets the INSERT proceed.
    """
    pending = list(state.pending)
    drafts = state.drafts
    decisions = list(state.decisions)
    consumed: set[int] = set()

    deletes: list[tuple[str, exp.Table]] = []
    for _, stmt in pending:
        if stmt.kind != "delete":
            continue
        node = _parse(stmt, state.dialect)
        table = _target_of(node)
        if table is not None:
            deletes.append((stmt.raw.source_file, table))

    for index, stmt in pending:
        if stmt.kind != "insert_select":
            continue
        node = _parse(stmt, state.dialect)
        if isinstance(node.this, exp.Schema):
            continue  # column list: Task 2's pairing target, not this pass's concern
        table = _target_of(node)
        if table is None:
            continue
        comparisons = {
            compare_targets(delete_table, table)
            for delete_file, delete_table in deletes
            if delete_file == stmt.raw.source_file
        }
        if "same" in comparisons:
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "append",
                    2,
                    action=(
                        f"deferred: DELETE + INSERT INTO {table.name} is a delete and insert "
                        "pair, not converted (catalog 2.3, deferred to slice 6c)"
                    ),
                    reason=(
                        "a delete-then-insert pair only replaces the rows the DELETE removed; "
                        "an append incremental re-inserts everything the model selects on every "
                        "run regardless of the DELETE, which would silently change which rows "
                        "survive a run"
                    ),
                )
            )
            continue
        if "ambiguous" in comparisons:
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "append",
                    2,
                    action=(
                        f"deferred: DELETE + INSERT INTO {table.name} may be a delete and "
                        "insert pair, but their qualification can't confirm it (catalog 2.3)"
                    ),
                    reason=(
                        "a DELETE and this INSERT name the same bare table but qualification "
                        "differs enough that they can't be confirmed as the same target from "
                        "the SQL alone — which schema an unqualified name resolves to depends "
                        "on the session's default, not on the script — so this is left pending "
                        "for a human to confirm rather than guessed either way"
                    ),
                )
            )
            continue
        select = node.expression
        body = select.sql(dialect=state.dialect, pretty=True)
        draft = ModelDraft(
            name=table.name,
            qualified_name=qualified_name(table),
            body=body,
            materialization="incremental",
            grants=(),
            source_indices=(index,),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
            incremental_strategy="append",
            unique_key=(),
        )
        drafts, verdict, existing = replace_draft(drafts, draft)
        consumed.add(index)
        if verdict is not None:
            assert existing is not None  # every non-None verdict has a prior draft
            decisions.append(
                _collision_decision(stmt, index, "append", 2, table.name, draft, verdict, existing)
            )
        if verdict == "superseded":
            continue
        reason = (
            "an append incremental re-inserts everything the model selects on every run "
            "unless the model's own SELECT filters to new rows; supply --unique-key to "
            "switch it to a merge incremental instead"
        )
        where = select.args.get("where") if isinstance(select, exp.Select) else None
        if where is not None:
            where_sql = where.this.sql(dialect=state.dialect)
            reason += (
                f" — the SELECT already carries a WHERE ({where_sql}), named here as the "
                "likely incremental filter for a human to confirm, not applied as one"
            )
        decisions.append(
            _decision(
                stmt,
                index,
                "append",
                2,
                action=(
                    f"INSERT INTO {table.name} became an incremental model "
                    "(incremental_strategy='append')"
                ),
                reason=reason,
                question=("Should rows be appended on every run, or deduplicated on a unique key?"),
                chosen="append every row",
                alternatives=("merge on a unique key",),
            )
        )
    return PassState(
        pending=tuple((i, s) for i, s in pending if i not in consumed),
        drafts=drafts,
        decisions=tuple(decisions),
        dialect=state.dialect,
    )
