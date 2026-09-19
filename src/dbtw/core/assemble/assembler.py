"""Turns model drafts into placed, named, ordered models for a target project."""

from __future__ import annotations

import dataclasses
import heapq
from collections.abc import Mapping
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbtw.core.assemble.layers import layer_roles, role_for
from dbtw.core.assemble.refs import references_in
from dbtw.core.assemble.resolve import resolve_references
from dbtw.core.assemble.rewrite import rewrite_body
from dbtw.core.assemble.types import AssembledModel, ProjectChange, SourceEntry, TableRef
from dbtw.core.assemble.variables import Variable, extract_variables
from dbtw.core.context import Detection, LayerInfo, ProjectContext
from dbtw.core.naming import is_qualified, qualified_name, same_identifier
from dbtw.core.passes.types import (
    Answer,
    Decision,
    ModelDraft,
    Option,
    PassState,
    SchemaTest,
    Subject,
    Tier,
    append_option,
    inline_option,
    merge_option,
    statement_index,
    var_option,
    verify_option,
)
from dbtw.core.projections import known_projections

# Fixed priority used once the role-appropriate layer is missing. "role" itself
# is always tried first by the caller; this is the order the remaining roles
# are tried in, skipping whichever one *was* the role already checked.
_FALLBACK_ROLE_ORDER = ("mart", "staging", "intermediate")


def _decision(
    kind: str,
    name: str,
    action: str,
    reason: str,
    plain_reason: str = "",
    # Tier 1 by default because most of what assemble does is mechanical --
    # placement, naming, dependency order: unambiguous mappings applied
    # without asking (RFC section 6). A Decision recording something this
    # conversion could NOT settle from the SQL asks for tier 2 explicitly, and
    # where a reader meets it turns on that: section 5.2 collapses Tier 1 into
    # "mechanical changes, with a count", the right treatment for a rename and
    # the wrong one for a gap the reader has to act on.
    tier: Tier = 1,
) -> Decision:
    return Decision(
        key=f"assemble.{kind}.{name}",
        tier=tier,
        action=action,
        reason=reason,
        source_file="",
        line_start=0,
        line_end=0,
        plain_reason=plain_reason,
    )


def _resolve_layer(
    role: str,
    roles: dict[str, LayerInfo | None],
    layers: tuple[LayerInfo, ...],
    name: str,
) -> tuple[LayerInfo | None, list[Decision]]:
    """The role's layer, or the nearest available one, with a Decision when it falls back."""
    layer = roles.get(role)
    if layer is not None:
        return layer, []

    fallback_role: str | None = None
    fallback_layer: LayerInfo | None = None
    for candidate_role in _FALLBACK_ROLE_ORDER:
        if candidate_role == role:
            continue
        candidate = roles.get(candidate_role)
        if candidate is not None:
            fallback_role, fallback_layer = candidate_role, candidate
            break

    if fallback_layer is not None:
        reason = (
            f"no {role} layer in the target project's model tree; "
            f"used the {fallback_role} layer instead"
        )
        action = f"placed {name} in the {fallback_role} layer (no {role} layer available)"
        return fallback_layer, [_decision("layer_fallback", name, action, reason)]

    if layers:
        # Last resort before giving up entirely: any real layer at all, picked
        # deterministically (ctx.layers is already sorted by name).
        any_layer = layers[0]
        reason = (
            f"no {role} layer, and no mart/staging/intermediate layer either, in the "
            f"target project's model tree; used the only layer found: {any_layer.name}"
        )
        action = (
            f"placed {name} in the {any_layer.name} layer "
            f"(no {role}/mart/staging/intermediate layer found)"
        )
        return any_layer, [_decision("layer_fallback", name, action, reason)]

    reason = f"no {role} layer in the target project's model tree; no other layer available"
    action = f"could not place {name} in a {role} layer; no layer available at all"
    return None, [_decision("layer_fallback", name, action, reason)]


def _final_name(
    draft_name: str,
    layer: LayerInfo | None,
    detections: Mapping[str, Detection],
) -> tuple[str, list[Decision]]:
    """The name this draft's model file will carry, and the Decision that says
    why it changed and what changing it leaves behind.

    The convention half is the easy half. The half that matters is the
    cutover: a rename here is not a migration. dbt writes the relation its own
    model names, `draft_name` is not that relation, and nothing in the
    generated project ever writes to it -- so the original stands exactly as
    it was, and every query, job and dashboard still selecting from it goes on
    selecting from it. A backend engineer in the persona walkthroughs read the
    rename as a Django `RenameModel` ("atomic, data-preserving, references
    updated for me") and reported that saying so plainly was the single piece
    of expectation-management that stopped a real incident; two of five asked
    for it unprompted (spec section 11.6).

    Both registers state it, in their own terms, because they are for two
    readers: the dbt-native one keeps the prefix evidence that answers "why
    was it renamed at all", which the plain one has no use for.

    Neither describes the escape hatch -- naming the model back to the
    original so dbt adopts the table already there. Section 11.6 records that
    as the scariest sentence on the page for both engineers, and it needs
    mechanics this slice cannot supply. Saying what happens is this Decision's
    job; offering a manoeuvre it cannot explain is not.

    Deliberately materialization-free in both registers. This function is
    handed a name and a layer, never a draft, so it cannot know whether dbt
    will create a table or a view for the model -- and it does not need to:
    the original is left alone either way. It also says what the conversion
    does rather than what the original contains, so the sentence stays true
    for a script whose target does not exist in the database yet.
    """
    if layer is None or layer.prefix is None:
        return draft_name, []
    if draft_name.startswith(layer.prefix):
        return draft_name, []
    final_name = f"{layer.prefix}{draft_name}"
    detection = detections.get(f"layer.{layer.name}.prefix")
    evidence = detection.evidence if detection is not None else f"{layer.prefix} prefix"
    action = f"renamed {draft_name} to {final_name} (prefix {layer.prefix!r} — {evidence})"
    # One fact per sentence, which is how these read best and also how they
    # are checked: `tests/unit/register_claims.py` scopes a negation forward
    # to the end of the sentence it sits in, so a sentence that denies one
    # thing cannot also be read as asserting another. An earlier version of
    # this comment asked future editors to place a comma where a test wanted
    # one; that was the test's shape being wrong, and it has been fixed rather
    # than accommodated.
    #
    # Neither register says what the original table CONTAINS, only what this
    # conversion does to it: the sentence has to stay true for a script whose
    # target does not exist in the database yet.
    reason = (
        f"the {layer.name} layer's models all share the {layer.prefix!r} prefix: {evidence}. "
        f"The rename is not a migration. dbt writes the relations its own models name, and "
        f"{draft_name} is not one of them, so dbt creates {final_name} beside it. "
        f"{draft_name} is left exactly as it was. Nothing in this project writes to it "
        f"again. Everything still selecting from {draft_name} goes on reading {draft_name}. "
        f"It never sees a row only {final_name} has. Repointing those readers at "
        f"{final_name} is manual work this conversion leaves to you."
    )
    plain_reason = (
        f"Renaming does not move the table. Afterwards there are two of them. {draft_name} "
        f"is left exactly as it was. {final_name} is the new one, and it is the only one "
        f"anything here writes to. A dashboard, a scheduled job or a saved query pointed at "
        f"{draft_name} goes on reading {draft_name}. It never sees a row that only "
        f"{final_name} has. Pointing each of those at {final_name} is a change someone has "
        f"to make by hand."
    )
    return final_name, [_decision("rename", draft_name, action, reason, plain_reason=plain_reason)]


def _row_source_queries(select: exp.Select) -> list[exp.Query]:
    """The derived tables this SELECT draws rows from -- the FROM and every
    JOIN, where the source is a subquery rather than a table.

    Found by node type rather than by argument key. sqlglot spells the key
    `from_` in the version pinned here and `from` in others, and reading it by
    name returned None under the pinned one -- silently, which cost the
    derived-table and CTE coverage until a shape test caught it.
    """
    queries: list[exp.Query] = []
    for value in select.args.values():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, (exp.From, exp.Join)) and isinstance(item.this, exp.Subquery):
                inner = item.this.this
                if isinstance(inner, exp.Query):
                    queries.append(inner)
    return queries


def _row_filters(query: exp.Query, dialect: str | None) -> tuple[str, ...]:
    """The conditions that decide which rows this model's own query returns.

    The query SPINE, not every `WHERE` in the tree: the outer query, the
    branches of a set operation, the CTEs that feed it, and any derived table
    it selects from. Each of those contributes rows to the model, so a
    condition on one is a condition on the model.

    What the spine deliberately excludes is the reason this is a walk rather
    than a `find_all`. A predicate inside a scalar subquery in the select list
    (`SELECT id, (SELECT max(v) FROM lookup l WHERE l.id = e.id) AS v`) filters
    the lookup, not the model: every row of the model's own source is still
    returned, with a null where nothing matched. Reporting it as this model's
    filter put a sentence in the report -- "a row that does not match is
    selected by no run at all" -- that the model file beside it contradicts.
    A `WHERE EXISTS (...)` is the mirror image: the outer condition IS the row
    filter and is reported whole, while descending into it reported the same
    filter twice and called the model "filtered by 2 WHERE clauses".

    Deduplicated, in spine order: CTEs, then derived tables, then this query's
    own WHERE -- which is source order for every shape the tests cover.
    """
    found: list[str] = []
    seen: set[int] = set()

    def visit(node: exp.Query) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        for cte in node.ctes:
            if isinstance(cte.this, exp.Query):
                visit(cte.this)
        if isinstance(node, exp.SetOperation):
            for branch in (node.this, node.expression):
                if isinstance(branch, exp.Query):
                    visit(branch)
            return
        if not isinstance(node, exp.Select):
            return
        for source in _row_source_queries(node):
            visit(source)
        where = node.args.get("where")
        if where is not None:
            found.append(where.this.sql(dialect=dialect))

    visit(query)
    return tuple(dict.fromkeys(found))


def _incremental_filter_caveat(
    draft: ModelDraft, final_name: str, dialect: str | None
) -> list[Decision]:
    """The Decision recording what an incremental model's copied-over filter
    costs, or none where there is no filter to record.

    Choosing merge fixes duplication and leaves the script's `WHERE` exactly
    where it was. dbt does not narrow that condition between runs, and this
    conversion deliberately does not wrap it in an `is_incremental()` guard --
    nothing here can tell a watermark from an ordinary business filter, and
    guarding the wrong one would change which rows the model produces
    (`passes.tier2.append_pass` records the same refusal from the other side,
    where it names the WHERE as evidence for a human to confirm). So the model
    re-reads everything the condition matches on every run, and a row that
    does not match it is selected by no run at all.

    This is not a fix for that gap. It is the gap given a Decision, which is
    the standing rule for anything this conversion leaves for the reader: it
    was the warehouse engineer's single change request in both persona rounds,
    the only finding to survive a full rebuild unaddressed, and until it is
    closed the walk must not be silent about it (spec section 11.6).

    Only for an incremental model. A `table` materialization is rebuilt from
    nothing every run by design, so re-reading everything the filter matches
    is what it is for -- nobody was promised a narrowing there and none is
    withheld.

    Emitted here rather than in the pass that built the draft, because the gap
    belongs to the model and not to the statement's shape: an INSERT and a
    MERGE whose USING subquery carries the same WHERE produce the same model
    with the same cost, and a caveat raised only where the append pass happens
    to look would cover half of them.

    Every condition on the query SPINE counts, and nothing else does --
    `_row_filters` above owns that rule and says what it keeps and drops. Two
    earlier versions got the scope wrong in opposite directions and both were
    found by a reader, not by a test: reading the outermost `WHERE` alone told
    the reader of a CTE, a UNION or a derived table nothing at all, and
    `find_all` then told the reader of a scalar-subquery predicate something
    the model file contradicts. The text below says these conditions decide
    which rows the model takes, which is true of a spine condition and false
    of any other, so the scope is what makes the sentence honest rather than
    the wording. `test_incremental_filter` pins both directions.

    Runs against the RAW draft body and so must run before `rewrite_body`:
    a body already rewritten into dbt Jinja does not parse, `parse_one` raises
    `SqlglotError`, and this would silently record nothing. That is a real
    dependency on the order in `assemble`, not an incidental one, and
    test_a_body_already_rewritten_into_jinja_yields_no_caveat is what makes
    reordering fail rather than go quiet. The refusal itself is right --
    with no readable body there is no condition to quote, and a caveat about
    a filter it could not read would be worse than silence -- but "should
    never happen" is not a reason to leave it untested.
    """
    if draft.incremental_strategy is None:
        return []
    try:
        node = sqlglot.parse_one(draft.body, read=dialect)
    except SqlglotError:
        return []
    if not isinstance(node, exp.Query):
        return []
    conditions = _row_filters(node, dialect)
    if not conditions:
        return []
    named = "; ".join(conditions)
    quoted = (
        f"the script's WHERE ({named})"
        if len(conditions) == 1
        else f"the script's {len(conditions)} WHERE clauses ({named})"
    )
    action = (
        f"caveat: {final_name} keeps {quoted} exactly as written, so every run re-selects "
        "everything matching it"
    )
    # `is_incremental()` is named in this function's docstring and nowhere in
    # the text below. It is a dbt word; the report's glossary heading promises
    # to define the dbt words the report uses; and the glossary provably
    # cannot take it, because every spelling of it contains `incremental`,
    # which is already a term, and test_no_term_s_spelling_is_hidden_inside_
    # another_s refuses a spelling nested inside another. Naming the category
    # instead costs a dbt reader nothing they cannot recover from the model.
    #
    # The conditions are quoted once, in a clause of their own, and never
    # inside a clause carrying a claim: a condition is arbitrary user SQL, and
    # `WHERE x IS NOT NULL` would drop a negation into the clause that says
    # every run re-reads the whole source.
    reason = (
        f"{final_name}'s filtering is the script's own, copied verbatim: {named}. Nothing "
        "here can tell whether it was meant as this model's incremental watermark. "
        "Narrowing a condition that was not meant as one would change which rows the model "
        "produces. So no guard was written around it. It stays a fixed condition: every "
        "run evaluates it over the whole of this model's source rather than over what "
        "arrived since the last run. The work re-done grows with the table. A row that "
        "does not match is selected by no run at all, however late it arrives. If the "
        "filtering compares a date, a row turning up afterwards carrying an earlier one "
        f"never enters {final_name}. Narrowing or guarding the filtering is manual work "
        "this conversion leaves to you."
    )
    plain_reason = (
        f"The filtering that decides which rows this takes -- {named} -- comes from your "
        'script, copied word for word and left alone. It does not mean "only what is new '
        'since last time". It means the same thing every time. So each run goes back over '
        "the whole of that table again, however much has piled up. A row that does not "
        "match is taken by no run at all, however late it turns up. If the filtering "
        "compares a date, a row arriving afterwards with an earlier date on it never gets "
        "in. Narrowing the filtering so that it asks only for what is new is something "
        "someone has to do by hand."
    )
    return [
        _decision(
            "incremental_filter",
            draft.name,
            action,
            reason,
            plain_reason=plain_reason,
            # Tier 2, with every other caveat, rather than Tier 1 with the
            # renames beside it. The mapping here is not the unambiguous kind
            # Tier 1 names: whether the condition was this model's watermark
            # or an ordinary business filter is exactly the intent this
            # conversion cannot settle from the SQL. It also decides where a
            # reader meets it -- section 5.2 collapses Tier 1 into a count,
            # and a finding that survived two persona rounds unaddressed is
            # not one to bury under "3 mechanical changes".
            tier=2,
        )
    ]


def _source_entries(
    drafts: tuple[ModelDraft, ...],
    refs: Mapping[str, tuple[TableRef, ...]],
    draft_names: set[str],
    ctx: ProjectContext,
) -> tuple[tuple[SourceEntry, ...], list[Decision]]:
    """External references become SourceEntry rows, or a Decision when they can't.

    A reference is external when it matches neither a draft in this change nor
    an existing model in the target project. An external reference without a
    schema is reported, never guessed at — inventing a schema would be a
    fabrication. An external, schema-qualified reference already declared as a
    source in the target project is skipped with a Decision citing where it's
    declared, instead of being duplicated.

    Matching a *qualified* reference (one with a db/schema and/or catalog)
    against a draft or an existing model is done by dotted qualified name
    only, never by bare name: a qualified reference can never actually be the
    CTE-free bare ModelInfo entries read off the target project's filesystem
    — those carry no schema information at all — and it can only be a draft
    in this change when that draft's own qualified_name matches exactly. An
    unqualified reference keeps matching by bare name, as before.

    A reference qualified by *catalog* (with or without a schema, e.g.
    `prod.raw.orders` or Snowflake's catalog-only `mydb..orders`) is never
    proposed as a source either: a SourceEntry is keyed by `(source_name,
    table)` only, with no catalog field, so stripping the catalog to look one
    up would silently collapse two different catalogs' tables (or, for a
    catalog-only ref, would have nothing to key a source by at all) onto the
    same source() call — the identity bug this module exists to avoid, one
    level up. It is left out of both the `external` and `unqualified`
    buckets; `resolve.py`'s per-model rewrite independently records the
    Decision that explains why it stays unresolved and as written.
    """
    existing_model_names = {m.name for m in ctx.existing_models}
    draft_qualified_names = {d.qualified_name for d in drafts}
    declared = {(s.source_name, s.table): s for s in ctx.existing_sources}

    external: dict[tuple[str, str], TableRef] = {}
    unqualified: set[str] = set()
    for draft in drafts:
        for ref in refs[draft.name]:
            if is_qualified(ref):
                if qualified_name(ref) in draft_qualified_names:
                    continue
                if ref.catalog:
                    continue
                external.setdefault((ref.db, ref.name), ref)
            else:
                if ref.name in draft_names or ref.name in existing_model_names:
                    continue
                unqualified.add(ref.name)

    decisions: list[Decision] = []
    entries: list[SourceEntry] = []
    for key in sorted(external):
        source_name, table = key
        ref = external[key]
        already_declared = declared.get(key)
        if already_declared is not None:
            decisions.append(
                _decision(
                    "source_dedup",
                    f"{source_name}.{table}",
                    f"{source_name}.{table} already declared as a source",
                    f"already declared as a source in {already_declared.declared_in}; "
                    "skipped to avoid duplicating it",
                )
            )
            continue
        entries.append(SourceEntry(source_name=ref.db, schema=ref.db, table=ref.name))

    for name in sorted(unqualified):
        decisions.append(
            _decision(
                "source_unqualified",
                name,
                f"{name} is read but not schema-qualified",
                f"{name} can't be declared as a source without a schema; inventing "
                "one would be a fabrication, so it stays as written until Tier 2 "
                "resolves it",
            )
        )

    entries.sort(key=lambda e: (e.source_name, e.table))
    return tuple(entries), decisions


def _topological(models: list[AssembledModel]) -> tuple[list[AssembledModel], list[Decision]]:
    """Kahn's algorithm, alphabetical tie-break; a cycle is recorded, never fatal.

    `models` must already carry unique names — assemble() dedupes final-name
    collisions before calling this. `by_name` is still built first and used
    as the source of truth for "how many models are there", so that even a
    same-named duplicate (silently folded here, by construction of a dict)
    can never manufacture a false, empty "cycle" report: the count it's
    compared against is the deduplicated one, not the raw input length.
    """
    by_name = {m.name: m for m in models}
    indegree = {name: len(m.depends_on) for name, m in by_name.items()}
    dependents: dict[str, list[str]] = {name: [] for name in by_name}
    for name, m in by_name.items():
        for dep in m.depends_on:
            if dep in dependents:
                dependents[dep].append(name)

    ready = [name for name, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    remaining = dict(indegree)
    ordered_names: list[str] = []
    while ready:
        name = heapq.heappop(ready)
        ordered_names.append(name)
        for dependent in sorted(dependents[name]):
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heapq.heappush(ready, dependent)

    decisions: list[Decision] = []
    if len(ordered_names) < len(by_name):
        remainder = sorted(set(by_name) - set(ordered_names))
        if remainder:  # never emit a cycle Decision naming no models
            decisions.append(
                _decision(
                    "cycle",
                    ",".join(remainder),
                    f"dependency cycle among {', '.join(remainder)}; kept in alphabetical order",
                    "a cycle among these models prevents a topological order; "
                    "falling back to alphabetical order instead of failing",
                )
            )
        ordered_names.extend(remainder)

    return [by_name[name] for name in ordered_names], decisions


def _keys_str(keys: tuple[str, ...]) -> str:
    return ", ".join(keys)


def _find_incremental_decision_index(
    decisions: tuple[Decision, ...], source_indices: tuple[int, ...], chosen_label: str
) -> int | None:
    """Locate the tier-2 Decision that recorded `chosen_label` for the
    statement that actually survived into this model -- the append question
    `append_pass` asked, or the merge question `merge_pass` asked.

    Matching on `chosen == "append every row"` alone is not enough: a
    *collision* -- two differently qualified tables sharing a bare name, say
    `staging.orders` beside `mart.orders` -- gets a full `chosen="append
    every row"` Decision recorded for BOTH statements, since `append_pass`
    only skips recording one for a "superseded" verdict. Matching the
    statement index embedded in the Decision's key against
    `AssembledModel.source_indices` (which always names the *surviving*
    statement) is what tells those two Decisions apart; text-matching the
    action against the model's name cannot, since a collision's two
    statements share that name by construction. `merge_pass` records its own
    question the same way, and collides the same way, so a merge model is
    matched by the same two facts with its own `chosen` label.

    Two INSERTs into the *same* target no longer reach here as a
    redefinition -- `collisions.written_earlier` defers the later one before
    it is ever drafted -- so a collision between different tables is now the
    only way two append Decisions can compete for one model.
    """
    wanted = set(source_indices)
    for i, dec in enumerate(decisions):
        if dec.chosen == chosen_label and statement_index(dec) in wanted:
            return i
    return None


_KeyStatus = Literal["matched", "ambiguous", "missing"]


def _key_status(key: str, projections: list[tuple[str, bool]]) -> tuple[_KeyStatus, str | None]:
    """Whether `key` (always unquoted -- a CLI flag value can never carry
    quoting) is one of `projections`' output names.

    `same_identifier` folds case unless either side was written quoted, so
    a *quoted* projection whose spelling matches `key` only case-
    insensitively (`SELECT "Order_Id"` against `--unique-key order_id`) is
    never a confident "matched" -- whether they're really the same column
    depends on how this warehouse folds unquoted identifiers, which the
    SQL text never reveals. That's the same unknowable-from-the-text-alone
    shape `naming.compare_targets` already carries a name for
    ("ambiguous"); guessing either "matched" or "missing" here would be
    exactly the kind of confident-but-wrong guess that module's docstring
    warns against.
    """
    ambiguous_name: str | None = None
    for name, quoted in projections:
        if same_identifier(key, False, name, quoted):
            return "matched", name
        if quoted and ambiguous_name is None and key.casefold() == name.casefold():
            ambiguous_name = name
    if ambiguous_name is not None:
        return "ambiguous", ambiguous_name
    return "missing", None


def _upgrade_to_merge(
    dec: Decision,
    draft_name: str,
    keys: tuple[str, ...],
    *,
    caveat: str,
    answered: _AnsweredKey | None,
) -> Decision:
    """Rewrite an append Decision into the merge upgrade that was chosen.

    `chosen`/`options` mirror the wording `merge_pass` already uses for
    its own script-derived merges ("merge on <keys>" / "append every row"),
    so a model's incremental history reads the same regardless of whether
    the merge came from the script, from the flag, or from an answer to
    this very question. Which of the two inputs asked for it is the one
    thing that does differ, and it is named: a report that credited
    `--unique-key` for a merge nobody passed a flag for would describe a
    run that never happened. `caveat` is appended to `reason` verbatim --
    callers own its exact wording, so this function never has to guess (and
    can never fabricate) what's actually true of the model's body
    (FINDING 6).

    The rewritten `options` carry the checked merge alongside the plain one
    wherever it can be honoured, exactly as the question that was answered
    offered it. This Decision is what the next screen renders, so an option
    dropped here is an answer that vanishes the moment any other answer is
    applied -- unreachable for every caller but the one that never saw the
    first result. It is also what keeps `chosen` accounted for when the answer
    was the checked one: a `chosen` naming an option the question does not
    offer reads as an answer nobody could have given.

    "Accounted for", not "answerable": these rewritten labels name the key
    ("merge on order_id, checked on every run") while the pristine question
    `answers` are validated against spells the same options without one, so
    re-sending a label read off THIS Decision is still refused, and is pinned
    by
    `test_re_sending_an_append_questions_rewritten_checked_label_is_refused_today`.
    The engine's side of that has not changed and is not wrong: it cannot know
    that two spellings are one answer. What changed is the caller's side --
    `Option.kind` is that stable identity now, and `passes.answer_for` turns a
    kind back into the label whichever question is being answered offers. A
    consumer reads `kind` off this rewritten Decision and resolves it against
    the pristine one, so no label text crosses between two runs. The round
    trip is
    `test_answer_for_is_how_a_caller_re_sends_an_answer_across_a_rebuild`.
    """
    merge_answer = merge_option(keys)
    # dbt's built-in `unique` test checks one column, so the checked answer
    # exists only for a single-column key -- the same rule the tier-2
    # questions offer it under.
    checked = (verify_option(keys),) if len(keys) == 1 else ()
    declared_test = answered.option.declares_test if answered is not None else ""
    if answered is not None:
        by = "an answer to this question"
        # The label as the answer sent it, not one re-derived from `keys`:
        # the option that asks for columns is the one that does not name
        # them, so naming it from the key it was given would report a
        # question the user was never asked.
        because = (
            f"this question was answered {answered.option.label!r}, naming "
            f"{_keys_str(keys)} as that key"
        )
    else:
        by = "--unique-key"
        because = "--unique-key was supplied on the command line"
    if declared_test:
        # Only an option naming a single key declares a test -- an answer
        # resolving one to more than one key is refused by
        # `_incremental_answers` before it reaches here, and one resolving to
        # no key at all never gets past the empty-key branch in
        # `_apply_unique_key` -- so `checked` holds the option this answer
        # took. The assertion narrows the tuple for the type checker; it is
        # not what makes a bad answer safe.
        assert checked
        chosen_option = checked[0]
        also = f", which also declares dbt's {declared_test} test on {keys[0]}"
        check_note = (
            f"; the answer asks dbt to check that key too, so a {declared_test} test on "
            f"{keys[0]} is declared beside this model"
        )
    else:
        chosen_option, also, check_note = merge_answer, "", ""
    return dataclasses.replace(
        dec,
        action=(
            f"INSERT INTO {draft_name} became an incremental model "
            f"(incremental_strategy='merge', unique_key={list(keys)!r}) — upgraded "
            f"from append by {by}{also}"
        ),
        reason=(
            f"{because}; an append incremental re-inserts everything the model "
            "selects on every run, so this model was switched to a merge on the "
            "given key instead" + caveat + check_note
        ),
        chosen=chosen_option.label,
        options=(merge_answer, append_option()) + checked,
        # The key this question now turns on. Every other field here says the
        # model merges on `keys`; a Subject still reporting no key would put
        # the rewritten Decision in the one shape `Subject`'s docstring
        # describes as an append that has not been answered yet -- and a
        # screen reading it that way renders a column picker on a question the
        # user has already answered. `candidates` is left alone: it is what the
        # model projects, which answering did not change.
        subject=(
            dataclasses.replace(dec.subject, columns=keys) if dec.subject is not None else None
        ),
    )


def _downgrade_to_append(dec: Decision, draft_name: str, keys: tuple[str, ...]) -> Decision:
    """Rewrite a merge Decision into the append an answer chose instead.

    Only an answer can get here: `--unique-key` never turns a merge into an
    append. The script's ON clause is still where `keys` came from, so the
    keyed merge stays on offer as the alternative -- worded with the columns
    it actually named rather than as the keyless "merge on a unique key", so
    the report keeps a record of which key the script proposed and the
    answer turned down. Both merges it turned down stay on offer, the
    checked one included where it can be honoured: an answer is a choice
    this run made, not one the next run is held to, and the question the
    next screen renders is this rewritten one.
    """
    append_answer = append_option()
    keys_str = _keys_str(keys)
    return dataclasses.replace(
        dec,
        action=(
            f"MERGE INTO {draft_name} became an incremental model "
            f"(incremental_strategy='append') — downgraded from a merge on {keys_str} "
            "by an answer to this question"
        ),
        reason=(
            f"this question was answered {append_answer.label!r}: {keys_str} came off "
            "the MERGE's ON clause, and the answer rejects it as identifying a row "
            "uniquely, so every row this model selects is appended instead of merged "
            "on that key"
        ),
        chosen=append_answer.label,
        options=(append_answer, merge_option(keys))
        + ((verify_option(keys),) if len(keys) == 1 else ()),
    )


def _confirm_with_check(
    dec: Decision, draft_name: str, keys: tuple[str, ...], option: Option
) -> Decision:
    """Rewrite a merge Decision for the answer that keeps its key and asks
    dbt to check it.

    The model is untouched -- this answer agrees with the merge the script's
    ON clause already built -- so only the record moves: `chosen` becomes the
    option that was taken, and the action names the test now declared beside
    the model, since a .yml gets written for it. `options` are left exactly
    as the question offered them: the answer was validated against that
    offer, so the option it took is already among them, and rebuilding a set
    that is already right is how the two spellings drift apart.
    """
    test = option.declares_test
    return dataclasses.replace(
        dec,
        action=(
            f"MERGE INTO {draft_name} became an incremental model "
            f"(incremental_strategy='merge', unique_key={list(keys)!r}), with dbt's "
            f"{test} test declared on {keys[0]}"
        ),
        reason=(
            f"this question was answered {option.label!r}: {_keys_str(keys)} came off the "
            "MERGE's ON clause and the answer keeps it as the unique key, and asks dbt to "
            f"check that claim -- a {test} test on {keys[0]}, declared beside this model, "
            "which fails once two rows share a value"
        ),
        chosen=option.label,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _AnsweredKey:
    """One model's answer to its own incremental question, already resolved.

    `option` is the option the answer named, as the question offered it, so a
    Decision can quote what was actually chosen rather than re-deriving a
    label from `keys` -- "merge on a unique key" with no columns and "append
    every row" both resolve to an empty `keys`, and re-deriving would report
    the second when the caller sent the first. Keeping the whole Option
    rather than its label also keeps `columns_prompt` to hand: whether the
    label leaves the key unsaid is the option's own answer to give, not
    something a reader of the label gets to decide.
    """

    option: Option
    keys: tuple[str, ...]  # empty for "append every row"


def _flag_overridden_decision(
    draft_name: str, keys_str: str, answered: _AnsweredKey, after: AssembledModel
) -> Decision:
    """Record that a per-model answer displaced the blanket `--unique-key`.

    Without it the flag goes unmentioned for this model: the report would
    show the answer's outcome and nothing at all about the key the command
    line asked for, leaving a run that was told two different things looking
    like it was only ever told one.

    `after` is the model as it came out, and it — not the fact that an
    answer was given — is what this wording follows. An answer can displace
    the flag and then be declined itself, since the body check refuses a key
    the model does not select whoever asked for it. A Decision reading "was
    answered 'merge on customer_id' instead" beside a model that is still a
    keyless append would contradict the file written next to it, so the
    declined case is worded from what the model actually ended up being
    rather than from what was asked for.
    """
    # The label as sent, plus the columns when the label is the one that
    # leaves them unsaid -- quoting "merge on a unique key" and stopping there
    # would leave the report unable to say which key displaced the flag.
    # Which option that is comes off the option itself (`columns_prompt`),
    # not off a comparison with a label spelled here.
    taken = f"{answered.option.label!r}"
    if answered.keys and answered.option.columns_prompt:
        taken += f" with {_keys_str(answered.keys)}"
    # What the model came out doing, in the plain register's terms and read
    # off `after` rather than off which branch this is. The dbt register can
    # spell it in dbt's own config vocabulary; this one cannot, and a reader
    # who has just been told a key was not used still has to be told what the
    # table does instead.
    left = (
        f"matching rows on {_keys_str(after.unique_key)}"
        if after.unique_key
        else "adding every row it finds, every run"
    )
    # The answer took effect exactly when the model came out carrying the key
    # it asked for -- an empty one for "append every row", the named one for a
    # merge.
    if after.unique_key == answered.keys:
        outcome = f"its own question was answered {taken} instead"
        aftermath = "so it was taken and the flag was not applied here"
        plain_aftermath = (
            f"So that answer was taken for {draft_name}, and {keys_str} was not used here. "
            f"{draft_name} is left {left}."
        )
    else:
        # Spelled in dbt's own config vocabulary, the same way `_upgrade_to_merge`
        # names an outcome, rather than in prose needing an article the strategy
        # name would have to agree with.
        if after.unique_key:
            left_as = (
                f"incremental_strategy={after.incremental_strategy!r}, "
                f"unique_key={list(after.unique_key)!r}"
            )
        else:
            left_as = f"incremental_strategy={after.incremental_strategy!r}, no unique_key"
        outcome = (
            f"its own question was answered {taken}, and that answer was not applied "
            f"either — the model is left as this conversion built it ({left_as})"
        )
        aftermath = (
            "so the flag was dropped for this model; the answer that displaced it was "
            "then declined on its own merits, for the reason recorded in the Decision "
            "beside this one"
        )
        plain_aftermath = (
            f"So {keys_str} was not used here. The answer that displaced it was then turned "
            "down for a reason of its own, written in the record beside this one. "
            f"{draft_name} was left {left}."
        )
    return Decision(
        key=f"assemble.unique_key_overridden.{draft_name}",
        tier=2,
        action=f"--unique-key {keys_str} was not applied to {draft_name}: {outcome}",
        reason=(
            "--unique-key answers every incremental question in this run at once, "
            "and this model's own question was answered separately; the answer "
            f"naming this one model is the more specific of the two, {aftermath}"
        ),
        plain_reason=(
            f"{keys_str} was asked for on the command line, which asks the same thing of "
            f"every table in this run at once. {draft_name} was asked about on its own, and "
            f"answered on its own. An answer about one table is the more particular of the "
            f"two. {plain_aftermath}"
        ),
        source_file="",
        line_start=0,
        line_end=0,
    )


def _requested_phrase(keys: tuple[str, ...], from_answer: bool) -> str:
    """How a Decision names the input that asked for `keys`.

    The flag-driven wording is unchanged from before answers existed --
    "--unique-key order_id" -- because it is what the report has always
    said and what the CLI tests read back.
    """
    if from_answer:
        return f"the answered unique key {_keys_str(keys)}"
    return f"--unique-key {_keys_str(keys)}"


def _apply_unique_key(
    models: list[AssembledModel],
    decisions: tuple[Decision, ...],
    unique_key: tuple[str, ...],
    final_to_draft_name: Mapping[str, str],
    dialect: str | None,
    answered_keys: Mapping[str, _AnsweredKey],
) -> tuple[list[AssembledModel], tuple[Decision, ...], list[Decision], list[SchemaTest]]:
    """Settle each incremental model's unique key from the two inputs that
    can name one: `answered_keys`, this run's answers to the models' own
    incremental questions, and `unique_key`, the blanket `--unique-key`.

    The two are the same question asked at two widths, so they meet here
    rather than in two code paths: an answer for a model supplies that
    model's key, `--unique-key` supplies every other model's, and a model
    neither of them names keeps the strategy the script gave it. Where both
    speak for one model the answer wins, being the more specific of the
    two, and `_flag_overridden_decision` records the flag that was dropped.
    An answer of "append every row" arrives here as an empty key tuple:
    for an append model that means stay as you are, and for a merge model
    it is the one thing `--unique-key` can never ask for, a downgrade off
    the script's own ON clause.

    A model that is already `merge` keeps its own key untouched -- its ON
    clause is better evidence of the true unique key than a blanket CLI
    flag -- but a Decision is still recorded when the flag disagrees with
    it, so the override that was declined is visible in the report. An
    append model is upgraded only when its own body backs up the key
    (FINDING 2/5/6): every key column must be a real output column of the
    model (matched case-insensitively, per `_key_status`, since neither a
    CLI value nor an answered column carries quoting), a star projection
    gets the key applied with an honest caveat instead of a confident
    match, a genuinely absent column blocks the upgrade outright, and a
    quoted output name that only differs by case is left ambiguous rather
    than guessed either way. That classification is the same whichever
    input named the key -- a key the model does not select fails at dbt run
    time no matter who asked for it -- so only the wording naming the input
    differs. Naming in every new Decision kind here uses the pre-rename
    draft name -- matching the convention every surrounding tier-2 Decision
    already uses (FINDING 3).

    An answer can also ask for the key to be checked rather than taken on
    trust ("...checked on every run"). It resolves to the same key the plain
    merge answer resolves to and takes the same path through here -- the
    model that comes out is the same file either way -- and adds one
    `SchemaTest` to the returned list, which is what Task 3 writes as a .yml
    beside the model. That test names the model by its FINAL name, which is
    what these models carry: the .yml's `models:` entry has to name a model
    dbt can resolve. It is recorded only where the merge was actually
    applied, so an answer the body check declined leaves no test claiming a
    check on a model that stayed an append.
    """
    keys_str = _keys_str(unique_key)
    decisions_list = list(decisions)
    extra_decisions: list[Decision] = []
    new_models: list[AssembledModel] = []
    schema_tests: list[SchemaTest] = []
    found_incremental = False

    for model in models:
        # None means "no answer for this model"; an empty tuple means "answered
        # 'append every row'". Both are falsy, and only the first falls back to
        # the flag, so they are told apart by identity, never by truthiness.
        answered = answered_keys.get(model.name)
        if model.incremental_strategy == "append":
            found_incremental = True
            draft_name = final_to_draft_name[model.name]
            model_keys = unique_key if answered is None else answered.keys
            if not model_keys:
                # Neither this model's own answer nor the flag names a key for
                # it. An append incremental with no unique key is exactly what
                # the script already produced, so it stays as it is.
                new_models.append(model)
                continue
            model_keys_str = _keys_str(model_keys)
            requested = _requested_phrase(model_keys, answered is not None)
            known = known_projections(model.body, dialect)

            if known is None:
                caveat = (
                    " (this model's body could not be parsed to confirm its output "
                    "columns, so this could not be verified)"
                )
            else:
                # An unnamed projection can never match a key, so this one
                # reader of the list has no use for `known_projections`'
                # third flag.
                projections, has_star, _unnamed = known
                # (key, status, matched-or-ambiguous-name) per key column --
                # built as one list, not zip(model_keys, statuses), so the
                # three views below can never drift out of alignment.
                statuses = [(k, *_key_status(k, projections)) for k in model_keys]
                missing = [k for k, status, _ in statuses if status == "missing"]
                ambiguous = [(k, name) for k, status, name in statuses if status == "ambiguous"]
                all_matched = all(status == "matched" for _, status, _ in statuses)

                if missing and not has_star:
                    absent = _keys_str(tuple(missing))
                    extra_decisions.append(
                        Decision(
                            key=f"assemble.unique_key_not_selected.{draft_name}",
                            tier=2,
                            action=(
                                f"{requested} was not applied to "
                                f"{draft_name}: it does not select {absent}"
                            ),
                            reason=(
                                "a merge's unique_key must be one of the model's own "
                                "output columns; forcing this key onto a model that "
                                "doesn't select it would fail at dbt run time, so it "
                                "was left as an append incremental instead"
                            ),
                            plain_reason=(
                                f"{draft_name} does not select {absent}, so nothing was set "
                                "up to match rows on it. Matching a new row against one "
                                "already in the table needs a column the model itself gives "
                                f"back. {draft_name} goes on adding every row it finds, every "
                                "run, which is what naming a column was meant to stop. Name a "
                                "column this model does select, or change the query so that "
                                f"it selects {absent}."
                            ),
                            source_file="",
                            line_start=0,
                            line_end=0,
                        )
                    )
                    new_models.append(model)
                    continue

                if ambiguous and not has_star and not missing:
                    named = ", ".join(f'{k} as "{name}"' for k, name in ambiguous)
                    # The two halves the plain register needs separately: what
                    # was asked for, and what the model writes. The dbt
                    # register pairs them with SQL's own `as`, which is the
                    # spelling this one may not lean on.
                    asked = _keys_str(tuple(k for k, _ in ambiguous))
                    written = ", ".join(f'"{name}"' for _, name in ambiguous)
                    extra_decisions.append(
                        Decision(
                            key=f"assemble.unique_key_ambiguous.{draft_name}",
                            tier=2,
                            action=(
                                f"{requested} was not applied to "
                                f"{draft_name}: whether it selects {named} is "
                                "ambiguous, not confirmed"
                            ),
                            reason=(
                                "a quoted output column is case-sensitive, so whether "
                                "it's really the same column as the unquoted key "
                                "value asked for can't be told from the SQL text "
                                "alone -- the same ambiguous/same/different tri-state "
                                "naming.compare_targets uses for cross-statement "
                                "target identity; left as an append incremental "
                                "rather than guessing either way"
                            ),
                            plain_reason=(
                                f"The column named was {asked}, written without quote marks. "
                                f"{draft_name} selects "
                                f"{written} instead, with quote marks around it, and quote "
                                "marks make the capital and small letters part of a column's "
                                "name. There is nothing in the text of the query that settles "
                                "whether those are one column spelled two ways or two "
                                "different columns. Guessing wrong would match rows on the "
                                f"wrong column, so nothing was set up to match on {asked} at "
                                f"all. {draft_name} goes on adding every row it finds, every "
                                "run. Ask for it with the same capital and small letters the "
                                "query uses, or take the quote marks off that column in the "
                                "query."
                            ),
                            source_file="",
                            line_start=0,
                            line_end=0,
                        )
                    )
                    new_models.append(model)
                    continue

                if has_star and not all_matched:
                    caveat = (
                        f" (this model selects *, so whether it actually projects "
                        f"{model_keys_str} could not be verified)"
                    )
                else:
                    caveat = ""

            index = _find_incremental_decision_index(
                decisions, model.source_indices, append_option().label
            )
            assert index is not None  # every append model has its own append_pass Decision
            decisions_list[index] = _upgrade_to_merge(
                decisions_list[index],
                draft_name,
                model_keys,
                caveat=caveat,
                answered=answered,
            )
            upgraded = dataclasses.replace(
                model, incremental_strategy="merge", unique_key=model_keys
            )
            # Recorded here, past every branch that declines the key, so a
            # test is only ever declared beside a model that really did come
            # out merged on the column it names. The name comes off the model
            # that is being appended -- the final, renamed one the .yml has to
            # name -- rather than off `draft_name`, which is the pre-rename
            # spelling the Decisions around it use.
            if answered is not None and answered.option.declares_test:
                schema_tests.append(
                    SchemaTest(
                        model=upgraded.name,
                        column=model_keys[0],
                        test=answered.option.declares_test,
                    )
                )
            new_models.append(upgraded)
        elif model.incremental_strategy == "merge":
            found_incremental = True
            draft_name = final_to_draft_name[model.name]
            if answered is not None:
                if answered.keys:
                    # The only keyed merge labels a merge question ever offers
                    # are its own script-derived one and the checked variant of
                    # it, so an answer that keeps the merge keeps exactly the
                    # key the model already carries. The two differ in one
                    # thing: whether dbt is asked to check that key.
                    if answered.option.declares_test:
                        # Offered only for a single-column key, since that is
                        # all dbt's built-in test can check.
                        #
                        # A raise, not an assert, and for the reason the
                        # writer's duplicate-source check gives: `python -O`
                        # strips an assert, and what an assert leaves behind
                        # here is the worst outcome available -- a test
                        # declared on keys[0] alone, recording a single-column
                        # claim the user never made, written to disk beside a
                        # model merged on more. `_incremental_answers` refuses
                        # a multi-column checked answer before anything is
                        # applied, so this is unreachable; that is why it is a
                        # bug rather than a usage error when it is reached.
                        if len(answered.keys) != 1:
                            raise MulticolumnCheckedAnswerError(
                                f"{model.name} took a checked answer naming "
                                f"{_keys_str(answered.keys)}; dbt's unique test checks one "
                                "column, and _incremental_answers refuses more than one "
                                "before applying anything, so reaching here is a dbtw bug."
                            )
                        index = _find_incremental_decision_index(
                            decisions, model.source_indices, merge_option(model.unique_key).label
                        )
                        assert index is not None  # registered by _incremental_answers
                        decisions_list[index] = _confirm_with_check(
                            decisions_list[index], draft_name, answered.keys, answered.option
                        )
                        schema_tests.append(
                            SchemaTest(
                                model=model.name,
                                column=answered.keys[0],
                                test=answered.option.declares_test,
                            )
                        )
                    new_models.append(model)
                    continue
                index = _find_incremental_decision_index(
                    decisions, model.source_indices, merge_option(model.unique_key).label
                )
                # _incremental_answers found this same Decision to register the
                # answer against it; there is no answer without one.
                assert index is not None
                decisions_list[index] = _downgrade_to_append(
                    decisions_list[index], draft_name, model.unique_key
                )
                new_models.append(
                    dataclasses.replace(model, incremental_strategy="append", unique_key=())
                )
                continue
            if unique_key and model.unique_key != unique_key:
                extra_decisions.append(
                    Decision(
                        key=f"assemble.unique_key_ignored.{draft_name}",
                        tier=2,
                        action=(
                            f"{draft_name} kept its script-derived unique_key "
                            f"({_keys_str(model.unique_key)}); --unique-key {keys_str} "
                            "was not applied"
                        ),
                        reason=(
                            "this model's own MERGE ON clause is stronger evidence of "
                            "its true unique key than a blanket --unique-key flag on "
                            "the command line, so the script-derived key was kept "
                            "instead of the flag's"
                        ),
                        plain_reason=(
                            "The statement this model came from already names the column "
                            f"{draft_name} matches rows on, which is "
                            f"{_keys_str(model.unique_key)}. {keys_str} was asked for on the "
                            "command line, which asks the same thing of every table in this "
                            "run at once. A column the statement itself names is evidence "
                            f"about this one table in particular. So {draft_name} goes on "
                            f"matching rows on {_keys_str(model.unique_key)}. {keys_str} was "
                            "not used here. To change that, change the statement in your "
                            "script."
                        ),
                        source_file="",
                        line_start=0,
                        line_end=0,
                    )
                )
            new_models.append(model)
        else:
            new_models.append(model)

    # The flag-was-overridden Decisions, emitted here rather than inside the
    # branches above, because until a model's branch has finished there is no
    # honest way to word one: an answer displaces the flag, and the body check
    # can then decline that answer, leaving a model the flag never touched and
    # the answer never changed. Both incremental branches route through here,
    # even though only the append one can be declined today: one emission point
    # cannot drift from the outcome it describes, and the wording is read off
    # the resulting model rather than assumed, so a decline added to the merge
    # path later is described correctly without changing anything here.
    #
    # Every branch above appends exactly one model, in order, so a model and
    # what became of it line up by position; `strict` makes that an error if it
    # ever stops being true rather than silently pairing the wrong two.
    for before, after in zip(models, new_models, strict=True):
        answered = answered_keys.get(before.name)
        if answered is None or not unique_key or answered.keys == unique_key:
            continue
        extra_decisions.append(
            _flag_overridden_decision(final_to_draft_name[before.name], keys_str, answered, after)
        )

    # Only the flag can go unused this way: an answer names one model's own
    # question, so it can never be left with nothing to apply to -- a key with
    # no model behind it is refused by assemble()'s validation gate instead.
    if unique_key and not found_incremental:
        extra_decisions.append(
            Decision(
                key="assemble.unique_key_unused",
                tier=2,
                action=(
                    f"--unique-key {keys_str} was supplied but no model in this "
                    "change is incremental"
                ),
                reason=(
                    "no model converted from this SQL has an append or merge "
                    "incremental strategy, so there was nothing for --unique-key to "
                    "apply to -- check for a typo in the flag or in the input SQL"
                ),
                plain_reason=(
                    "Nothing this conversion built is brought up to date by adding to what "
                    f"is already in it, so there was nothing for {keys_str} to be used on. "
                    "Matching rows on a column is only a choice for a table that is added "
                    "to; every table here is worked out again from nothing each time. "
                    "Check the spelling of what was typed, and check that the script really "
                    "does add rows to a table that is already there."
                ),
                source_file="",
                line_start=0,
                line_end=0,
            )
        )

    return new_models, tuple(decisions_list), extra_decisions, schema_tests


def _incremental_answers(
    models: list[AssembledModel],
    decisions: tuple[Decision, ...],
    answers: Mapping[str, Answer],
) -> tuple[dict[str, _AnsweredKey], list[Decision]]:
    """Pair each incremental model with the tier-2 question that produced it,
    and read this run's answer to that question.

    Returns the unique key each answered model's own answer chose, keyed by
    final model name and empty for "append every row", together with every
    incremental question an answer may legitimately name this run.

    Both come out of the same walk deliberately. A question is answerable
    exactly when an answer to it would reach `_apply_unique_key`, which
    iterates these same models -- so an inherited question whose statement
    never became a model (deferred, superseded, or dropped as a final-name
    collision) is neither registered nor applied, and the two can't drift
    into the two failure modes that matter: a legitimate answer refused, or
    an answer accepted for a question nothing acts on.

    This is also where an answer is refused for asking a checked option to
    check more than the one column dbt's built-in test can check. That
    refusal cannot wait for assemble()'s validation gate, which runs once the
    answers collected here have already been applied.
    """
    answered_keys: dict[str, _AnsweredKey] = {}
    questions: list[Decision] = []
    append_label = append_option().label
    for model in models:
        if model.incremental_strategy == "append":
            chosen_label = append_label
        elif model.incremental_strategy == "merge":
            chosen_label = merge_option(model.unique_key).label
        else:
            continue
        index = _find_incremental_decision_index(decisions, model.source_indices, chosen_label)
        if index is None:
            continue
        dec = decisions[index]
        if not dec.question or not dec.options:
            continue
        questions.append(dec)

        answer = answers.get(dec.key)
        chosen_option = (
            next((o for o in dec.options if o.label == answer.label), None) if answer else None
        )
        if chosen_option is None:
            # A label this question never offered is refused by assemble()'s
            # validation gate, so it is left alone here rather than guessed
            # into one of the options it might have meant.
            continue
        assert answer is not None  # chosen_option is None whenever answer is
        if chosen_option.columns_prompt:
            # The option that says it cannot settle its own key, so the one
            # option that reads `columns`. An empty tuple here is an answer
            # with no key at all; it reaches the gate below, which refuses it
            # rather than writing a merge with an empty unique_key.
            keys = answer.columns
        elif answer.label == append_label:
            keys = ()
        else:
            # A keyed merge label ("merge on order_id"), which a question only
            # ever offers for the key the model it belongs to already carries
            # -- so the label names the key and `columns` has nothing to add.
            keys = model.unique_key
        if chosen_option.declares_test and len(keys) > 1:
            # Refused here, where the answer is resolved, and not in
            # assemble()'s validation gate with the other two column rules:
            # that gate runs at the end, after `_apply_unique_key` has already
            # applied everything collected here, and an option that declares
            # dbt's one-column test has no check to build for a two-column
            # key. Applied first and refused afterwards, this input is a crash
            # rather than a refusal -- so the rule has to sit in front of the
            # application, not behind it.
            #
            # Only the upper bound is checked here. An answer with no columns
            # at all is the `columns_prompt` rule's, and the gate refuses it
            # with the prompt naming what is missing, which is the more useful
            # of the two messages for a caller who supplied nothing.
            raise UnknownAnswerError(
                f"{dec.key} was answered {answer.label!r} with {_keys_str(keys)}; that "
                f"option declares dbt's {chosen_option.declares_test} test, which checks "
                "one column, so it can only be answered with one column"
            )
        answered_keys[model.name] = _AnsweredKey(option=chosen_option, keys=keys)
    return answered_keys, questions


class UnknownAnswerError(ValueError):
    """An answer names a Decision this run did not produce, an option that
    Decision did not offer, or columns that option cannot use. Refused rather
    than ignored: an ignored answer leaves the caller believing a choice was
    applied that never was.

    Callers passing `answers` to `assemble` must know one contract, because
    breaking it surfaces here and nowhere else. An answer names a
    `Decision.key` a *previous* run of the same conversion handed out, and a
    tier-2 key embeds the path of the file the statement was read from
    ("tier2.append.<source_file>:<index>"). The keys are therefore stable only
    for as long as the caller presents the same SQL at the same path: read the
    same script from a fresh temporary directory on the next run and every key
    issued by the first one becomes unknown, and every answer held against
    them is refused here. A caller that stages uploads (a web layer, say) must
    give a conversion one durable path and reuse it across runs, not copy the
    file somewhere new each time an answer comes back.
    """


class MulticolumnCheckedAnswerError(ValueError):
    """A checked answer reached application naming more than one column.

    Unreachable: `_incremental_answers` refuses a multi-column checked answer
    before any of them are applied, so this guards the gap between that gate
    and the code that trusts it. Unlike `UnknownAnswerError` it is NOT a usage
    error and is deliberately absent from the CLI's `_USAGE_ERRORS` -- no
    input produces it, so reaching it means a dbtw bug and should surface as
    one. A raise rather than an assert because `python -O` strips asserts,
    and what silence leaves here is a `unique` test declared on one column of
    a key made of several: a claim about the user's data that the user never
    made, written to disk.
    """


def assemble(
    state: PassState,
    ctx: ProjectContext,
    *,
    inline_vars: bool = False,
    unique_key: tuple[str, ...] = (),
    # Keyed by the Decision.key a previous run of this same conversion handed
    # out. Those keys are only stable while the source SQL keeps the same path
    # -- see UnknownAnswerError, which is where breaking that surfaces.
    answers: Mapping[str, Answer] | None = None,
) -> ProjectChange:
    new_decisions: list[Decision] = []
    drafts: tuple[ModelDraft, ...] = state.drafts
    draft_names = {d.name for d in drafts}

    # Step 1
    refs = {d.name: references_in(d.body, state.dialect) for d in drafts}

    # Step 2. A qualified reference (has a db/schema and/or catalog) can only
    # be a dependency on a draft whose own qualified_name matches it exactly
    # — its bare name matching some unrelated draft's bare name is not enough
    # (e.g. a read of raw.orders is not a dependency on a draft that merely
    # happens to be named "orders" while targeting analytics.orders, and a
    # catalog-only read of mydb..orders is not a dependency on a draft merely
    # named "orders" either). An unqualified reference keeps matching by bare
    # draft name, as before.
    qualified_to_draft_name = {d.qualified_name: d.name for d in drafts}
    deps: dict[str, frozenset[str]] = {}
    for name in refs:
        dep_names: set[str] = set()
        for r in refs[name]:
            if is_qualified(r):
                dep_draft_name = qualified_to_draft_name.get(qualified_name(r))
                if dep_draft_name is not None and dep_draft_name != name:
                    dep_names.add(dep_draft_name)
            elif r.name in draft_names and r.name != name:
                dep_names.add(r.name)
        deps[name] = frozenset(dep_names)
    dependents: dict[str, set[str]] = {name: set() for name in refs}
    for name, deps_for_name in deps.items():
        for dep in deps_for_name:
            dependents[dep].add(name)
    dependents_frozen = {name: frozenset(deps_of) for name, deps_of in dependents.items()}

    source_entries, source_decisions = _source_entries(drafts, refs, draft_names, ctx)
    new_decisions.extend(source_decisions)

    roles = layer_roles(ctx)
    detections_by_key = {d.key: d for d in ctx.detections}
    existing_by_name = {m.name: m for m in ctx.existing_models}

    final_names: dict[str, str] = {}
    # Keyed by the draft's own position in `drafts`, not by draft.name: two
    # drafts can legitimately share a name (e.g. two same-named drafts that
    # also collide on final name below), and a name-keyed dict would let the
    # second draft processed silently overwrite the first's own placement —
    # surfacing the wrong draft's body/path/materialization for whichever
    # draft is looked up by that shared name, even for the survivor.
    placed: dict[int, AssembledModel] = {}
    # Also keyed by position, for the same reason. Collecting each draft's
    # own placement Decisions here, instead of appending them to
    # new_decisions immediately, lets a dropped draft's Decisions be
    # discarded wholesale once dropped_indices is known — a decision keyed
    # and worded around a model that was never written is not a fix, it's a
    # different bug.
    placement_decisions: dict[int, list[Decision]] = {}

    for draft_index, draft in enumerate(drafts):
        local_decisions: list[Decision] = []
        role = role_for(draft.name, deps, dependents_frozen)
        layer, layer_decisions = _resolve_layer(role, roles, ctx.layers, draft.name)
        local_decisions.extend(layer_decisions)

        final_name, name_decisions = _final_name(draft.name, layer, detections_by_key)
        local_decisions.extend(name_decisions)
        final_names[draft.name] = final_name

        existing = existing_by_name.get(final_name)
        if existing is not None:
            local_decisions.append(
                _decision(
                    "collision",
                    draft.name,
                    f"{final_name} already exists in the target project at {existing.path}",
                    "a model with this final name is already present in the target project",
                )
            )

        if layer is not None:
            path = f"{layer.path}/{final_name}.sql"
        else:
            base = ctx.model_paths[0] if ctx.model_paths else "models"
            path = f"{base}/{final_name}.sql"
            local_decisions.append(
                _decision(
                    "path",
                    draft.name,
                    f"placed {final_name} at {path} — no layer resolved for it",
                    f"the target project has no layers at all, so there is nowhere to "
                    f"place {final_name}; fell back to the first configured model-path, "
                    f"and AssembledModel.layer records the role ({role}) since there is "
                    f"no real layer name to record",
                )
            )

        if (
            layer is not None
            and draft.materialization == layer.materialization
            and draft.incremental_strategy is None
        ):
            materialization = None
            local_decisions.append(
                _decision(
                    "materialization",
                    draft.name,
                    f"materialized config omitted for {final_name} "
                    f"(matches the {layer.name} layer default of {layer.materialization!r})",
                    "materialization matches the layer's detected default; "
                    "config omitted so the project default takes over",
                )
            )
        else:
            materialization = draft.materialization

        # Into this draft's own list, not straight into new_decisions: a draft
        # dropped for a final-name collision below has its file never written,
        # and a caveat about the filter in a model nobody will read is one
        # more thing the report says that is not there.
        local_decisions.extend(_incremental_filter_caveat(draft, final_name, state.dialect))

        placed[draft_index] = AssembledModel(
            name=final_name,
            path=path,
            body=draft.body,
            materialization=materialization,
            grants=draft.grants,
            layer=layer.name if layer is not None else role,
            depends_on=(),  # filled in below, once every draft has a final name
            leading_comments=draft.leading_comments,
            source_indices=draft.source_indices,
            incremental_strategy=draft.incremental_strategy,
            unique_key=draft.unique_key,
        )
        placement_decisions[draft_index] = local_decisions

    # Two drafts can resolve to the same final name (e.g. one gets prefixed
    # into the other's own name). A dbt model is one file, so — before the
    # model list is built — keep only the file-order-later draft (highest
    # max(source_indices), same precedent as tier1's _replace_draft) and
    # record an honest Decision naming both for every draft that's dropped.
    # Grouped by the folded final name, not the final name as spelled. Two
    # models whose names differ only in case are two models to dbt but one
    # file on a case-insensitive filesystem, where the second write silently
    # replaces the first — so they are resolved here, with the loss recorded,
    # rather than left for the filesystem to resolve in silence.
    by_final_name: dict[str, list[tuple[int, ModelDraft]]] = {}
    for draft_index, draft in enumerate(drafts):
        by_final_name.setdefault(final_names[draft.name].casefold(), []).append(
            (draft_index, draft)
        )

    # Indices, not names: two colliding drafts can share a name (see above),
    # and dropping the loser's NAME would blacklist the winner too, since
    # `draft.name in dropped_names` can't tell them apart — the winner would
    # be filtered out right along with the loser, emitting zero models while
    # the Decision below claims one survived.
    dropped_indices: set[int] = set()
    for group in by_final_name.values():
        if len(group) == 1:
            continue
        kept_index, kept = group[0]
        for candidate_index, candidate in group[1:]:
            if max(candidate.source_indices) >= max(kept.source_indices):
                kept_index, kept = candidate_index, candidate
        for draft_index, draft in group:
            if draft_index == kept_index:
                continue
            dropped_indices.add(draft_index)
            dropped_final, kept_final = final_names[draft.name], final_names[kept.name]
            resolves = (
                f"both resolve to model {kept_final}"
                if dropped_final == kept_final
                else (
                    "resolve to model names differing only in case "
                    f"({dropped_final} and {kept_final})"
                )
            )
            new_decisions.append(
                _decision(
                    "collision",
                    draft.name,
                    f"{draft.name} and {kept.name} {resolves} — kept {kept.name}",
                    "a dbt model is one file, and on a case-insensitive filesystem two "
                    "names differing only in case are one file too — only one definition "
                    "can survive; resolve this collision in the source SQL",
                )
            )

    # Only a kept draft's own placement Decisions are real — a dropped
    # draft's file was never written, so its rename/collision/path/
    # materialization Decisions above are discarded; the "both resolve to"
    # Decision just recorded is the only honest record of it.
    for draft_index in range(len(drafts)):
        if draft_index in dropped_indices:
            continue
        new_decisions.extend(placement_decisions[draft_index])

    # Step 8: translate dependency names to final names now that every draft has one.
    models: list[AssembledModel] = []
    final_to_draft_name: dict[str, str] = {}
    for draft_index, draft in enumerate(drafts):
        if draft_index in dropped_indices:
            continue
        model = placed[draft_index]
        depends_on = tuple(sorted({final_names[dep] for dep in deps[draft.name]}))
        models.append(dataclasses.replace(model, depends_on=depends_on))
        final_to_draft_name[model.name] = draft.name

    ordered, cycle_decisions = _topological(models)
    new_decisions.extend(cycle_decisions)

    # Read once, here, rather than at each of the two stages that consult it:
    # the incremental questions just below, and the variable questions further
    # down.
    answers_map = answers or {}
    # Decisions an answer can actually be validated and applied against in this
    # run -- and only those. Collected structurally, as each one is matched to
    # the model it decides, rather than picked out of all_decisions afterwards
    # by key shape: an inherited Decision can carry a `question` and `options`
    # without this run having anything to apply an answer to (its statement was
    # deferred, superseded, or dropped), and such a question must not validate
    # as answerable. Registering a question here and applying its answer below
    # are two halves of one thing -- register without applying and a caller
    # believes a choice was taken that never was; apply without registering and
    # a legitimate answer is refused.
    answerable_decisions: list[Decision] = []

    inherited_decisions = state.decisions
    incremental_answers, incremental_questions = _incremental_answers(
        ordered, inherited_decisions, answers_map
    )
    answerable_decisions.extend(incremental_questions)
    # The dbt tests this run's answers asked for. Only an answer can ask for
    # one, so a run with no answers carries none -- `--unique-key` takes a key
    # on trust exactly as it always has.
    schema_tests: list[SchemaTest] = []
    if unique_key or incremental_answers:
        ordered, inherited_decisions, unique_key_decisions, schema_tests = _apply_unique_key(
            ordered,
            inherited_decisions,
            unique_key,
            final_to_draft_name,
            state.dialect,
            incremental_answers,
        )
        new_decisions.extend(unique_key_decisions)

    # From here on, everything above (placement, naming, dependency edges, and
    # source entries) has been computed from the RAW bodies — required, since
    # a rewritten body no longer re-parses as SQL. The rewrite stage below is
    # deliberately the last thing that touches a model's body.

    # Step 2: pull script variables out of pending; consumed statements never
    # come back around the pipeline again. A spark/databricks SET VAR/SET
    # VARIABLE statement is never consumed (its read-back form is a bare
    # identifier, ambiguous with a column reference); extract_variables
    # records the Decision that explains why instead.
    variables_found, consumed_indices, spark_deferral_decisions = extract_variables(
        state.pending, state.dialect
    )
    new_decisions.extend(spark_deferral_decisions)
    consumed = set(consumed_indices)
    remaining_pending = tuple(item for item in state.pending if item[0] not in consumed)

    declared_var_names = {name for name, _ in ctx.vars_declared}
    variable_defaults: dict[str, str | None] = {}
    # Whether this run *wants* each variable's default inlined, keyed the
    # same as variable_defaults but decided once per name (first occurrence)
    # and never touched by the DECLARE-then-SET backfill below. It records a
    # preference, not an outcome: whether a variable is actually inlined is
    # settled after this loop, once variable_defaults holds its final
    # backfilled value. Every key ever written to variable_defaults gets a
    # matching entry here (both branches below set it before touching
    # variable_defaults), so effective_variable_defaults can index it
    # directly instead of falling back to inline_vars -- a fallback would
    # silently revert to blanket-flag semantics for a future case that adds
    # to variable_defaults without also recording an inline preference.
    variable_inline: dict[str, bool] = {}
    # The same Option the questions below offer, built once: it is what an
    # answer is recognised by here and what the question offers there, and a
    # second hand-spelled copy of its label would let those two drift. The
    # gate accepts any label the question offers, so a rename that reached
    # only one of them would leave the answer accepted and then quietly
    # unapplied -- reported as the option nobody chose. Nothing about it
    # varies per variable, so it is built outside the loop.
    inline_answer = inline_option()
    # Each name's first occurrence, in the order they were found. The
    # emission pass below walks this instead of variables_found: it is the
    # order the Decisions are reported in, and each entry is the spelling
    # (source file and line) the report attributes that variable to.
    first_occurrences: list[Variable] = []
    seen_var_names: set[str] = set()
    for variable in variables_found:
        if variable.name in seen_var_names:
            # A later statement for an already-seen variable (e.g. DECLARE
            # followed by SET on the very next line) never gets its own
            # Decision — only the first occurrence is reported — but
            # extraction preserves statement order, so a later non-None
            # default still fills in a previously recorded None instead of
            # being silently discarded: `DECLARE @cutoff DATE; SET @cutoff =
            # '2024-06-30';` must report and inline '2024-06-30', not the
            # DECLARE's empty default (FINDING 4 — the most common T-SQL
            # parameter idiom).
            # A variable already declared in the target project (below) is
            # never filled in here either — it's pinned to None so it can
            # never be inlined, and a later local default must not undo that.
            if (
                variable.name not in declared_var_names
                and variable_defaults.get(variable.name) is None
                and variable.default_sql is not None
            ):
                variable_defaults[variable.name] = variable.default_sql
            continue
        seen_var_names.add(variable.name)
        first_occurrences.append(variable)

        if variable.name in declared_var_names:
            # Already declared in the target project's own vars — never
            # inline it, even with --inline-vars: doing so would silently
            # override the project's own declared value with whatever this
            # particular script happened to set locally. Pin the rewrite's
            # variable map to None (a var-only marker) so rewrite_body always
            # renders var(), matching what this Decision actually says
            # (FINDING 6 — Decision and disk must agree).
            variable_defaults[variable.name] = None
            variable_inline[variable.name] = False
            continue

        # A per-decision answer, when one is given for this variable, wins
        # over the run's blanket --inline-vars default -- it is the same
        # question, answered one variable at a time instead of for all of
        # them at once.
        answer = answers_map.get(f"assemble.variable.{variable.name}")
        variable_inline[variable.name] = (
            answer.label == inline_answer.label if answer else inline_vars
        )
        variable_defaults[variable.name] = variable.default_sql

    # variable_defaults holds each variable's best-known literal regardless of
    # whether it gets inlined -- a DECLARE-then-SET backfill (above) can fill
    # it in after the preference for that name was already recorded. Fold the
    # two together only now, once variable_defaults has taken its final
    # value: a variable this run chose not to inline renders as var() even if
    # a later statement backfilled a literal for it.
    effective_variable_defaults = {
        name: (default_sql if variable_inline[name] else None)
        for name, default_sql in variable_defaults.items()
    }

    # Every variable Decision, and every var this change declares, is emitted
    # here rather than inside the loop above, and reads its outcome off
    # effective_variable_defaults -- the same map, and the only map,
    # rewrite_body renders the bodies from. A Decision written inside the
    # loop had to predict that outcome before the DECLARE-then-SET backfill
    # could change it, and got it wrong: `DECLARE @cutoff DATE; SET @cutoff =
    # '2024-06-30';` under --inline-vars spliced the backfilled literal into
    # the body while the Decision beside it said there was no literal to
    # inline and the var was kept, and dbt_project.yml then declared a var no
    # model referenced. Deriving the wording, the declared vars and the body
    # from one map is what makes that disagreement unrepresentable rather
    # than merely fixed for this one input.
    kept_variables: list[Variable] = []
    for variable in first_occurrences:
        if variable.name in declared_var_names:
            new_decisions.append(
                Decision(
                    key=f"assemble.variable.{variable.name}",
                    tier=2,
                    action=(
                        f"{variable.name} already declared as a var in the target project; "
                        "its reference was rewritten to var(), not re-declared"
                    ),
                    reason=(
                        f"{variable.name} is already declared in the target project's vars; "
                        "declaring it again would duplicate it"
                    ),
                    source_file=variable.source_file,
                    line_start=variable.line_start,
                    line_end=variable.line_start,
                )
            )
            continue

        keep_answer = var_option(variable.name)
        if effective_variable_defaults[variable.name] is not None:
            chosen = inline_answer.label
            action = f"inlined {variable.name}'s literal default value in place of the parameter"
        else:
            chosen = keep_answer.label
            if variable_inline[variable.name]:
                # --inline-vars (or an answer resolving the same way) only
                # actually inlines when there is a literal default to inline.
                # A default-less variable (e.g. `DECLARE @region VARCHAR`
                # with nothing assigned, and no later SET) has no SQL to
                # splice in — rewrite_body renders a var() call regardless —
                # so claiming "inlined region's literal default value" would
                # be a lie the body doesn't back up (FINDING 5). Treat it
                # exactly like the keep-as-var case, honestly worded.
                action = (
                    f"{variable.name} has no default in the source SQL, so there is no "
                    "literal value to inline; kept as a dbt var instead"
                )
            else:
                action = (
                    f"declared {variable.name} as a dbt var, referenced via var('{variable.name}')"
                )
            # The body renders var('<name>') for exactly the variables this
            # map leaves at None, so exactly those must reach dbt_project.yml:
            # skipping one would leave the emitted var() call undeclared and
            # break `dbt compile`, and declaring one the body never calls
            # would put a var nobody references in the project. The
            # declaration carries the best-known literal as its default —
            # the backfilled one, where a later statement supplied it.
            kept_variables.append(
                Variable(
                    name=variable.name,
                    default_sql=variable_defaults[variable.name],
                    source_file=variable.source_file,
                    line_start=variable.line_start,
                )
            )

        variable_decision = Decision(
            key=f"assemble.variable.{variable.name}",
            tier=2,
            action=action,
            reason=(
                f"{variable.name} is a script parameter with no fixed value in the "
                "source SQL; it can be kept as a run-time dbt var or inlined as a "
                "literal constant"
            ),
            source_file=variable.source_file,
            line_start=variable.line_start,
            line_end=variable.line_start,
            question=f"Is {variable.name} a run-time parameter or a constant?",
            plain_question=(
                f"Should {variable.name} always be the value your script used, or "
                "something you choose each time you run it?"
            ),
            chosen=chosen,
            options=(inline_answer, keep_answer),
            subject=Subject(table=variable.name),
        )
        new_decisions.append(variable_decision)
        answerable_decisions.append(variable_decision)

    # Step 3: resolution maps, built from the assembled models (their final
    # names), the target project's existing models/sources, and the sources
    # this change itself proposes.
    draft_to_final = {d.name: final_names[d.name] for d in drafts}
    qualified_to_final = {d.qualified_name: final_names[d.name] for d in drafts}
    existing_model_names = frozenset(m.name for m in ctx.existing_models)
    declared_sources_map = {(s.source_name, s.table): s.source_name for s in ctx.existing_sources}
    proposed_sources_map = {(e.source_name, e.table): e.source_name for e in source_entries}

    # Step 4 + 5: rewrite each model's body into dbt Jinja, last, and record
    # a Decision for every rewrite and every reference left unresolved.
    rewritten_models: list[AssembledModel] = []
    for model in ordered:
        draft_name = final_to_draft_name[model.name]
        model_refs = refs[draft_name]
        resolutions = resolve_references(
            model_refs,
            draft_to_final=draft_to_final,
            qualified_to_final=qualified_to_final,
            existing_models=existing_model_names,
            declared_sources=declared_sources_map,
            proposed_sources=proposed_sources_map,
        )
        resolutions_by_key = {(r.ref.catalog, r.ref.db, r.ref.name): r for r in resolutions}
        rewritten_body = rewrite_body(
            model.body,
            state.dialect,
            resolutions_by_key,
            # Carries None for every name this run decided not to inline, so
            # the map alone decides each variable's form -- the per-variable
            # decision, never a blanket flag.
            effective_variable_defaults,
        )
        rewritten_models.append(dataclasses.replace(model, body=rewritten_body))

        ref_resolutions = [r for r in resolutions if r.kind == "ref"]
        source_resolutions = [r for r in resolutions if r.kind == "source"]
        unresolved_resolutions = [r for r in resolutions if r.kind == "unresolved"]

        summary_parts = []
        if ref_resolutions:
            summary_parts.append(f"{len(ref_resolutions)} reference(s) rewritten to ref()")
        if source_resolutions:
            summary_parts.append(f"{len(source_resolutions)} reference(s) rewritten to source()")
        if not summary_parts:
            summary_parts.append("no references needed rewriting")
        chosen_parts = [
            f"{r.ref.name}: {r.reason}" for r in (*ref_resolutions, *source_resolutions)
        ]

        new_decisions.append(
            Decision(
                key=f"assemble.rewrite.{model.name}",
                tier=2,
                action=f"rewrote {model.name}'s body: " + ", ".join(summary_parts),
                reason=(
                    "raw table references were mapped to this project's models and "
                    "sources so the body runs as dbt Jinja"
                ),
                source_file="",
                line_start=0,
                line_end=0,
                chosen="; ".join(chosen_parts) if chosen_parts else "no rewrite applied",
            )
        )

        for r in unresolved_resolutions:
            label = qualified_name(r.ref) or r.ref.name
            new_decisions.append(
                Decision(
                    key=f"assemble.rewrite_unresolved.{model.name}.{label}",
                    tier=2,
                    action=(
                        f"{label} in {model.name} left as written; could not resolve it "
                        "to ref() or source()"
                    ),
                    reason=r.reason,
                    source_file="",
                    line_start=0,
                    line_end=0,
                )
            )

    all_decisions = inherited_decisions + tuple(new_decisions)

    # Validated last, once answerable_decisions is complete: an answer names
    # a Decision key from a *previous* run of this same conversion, and that
    # run's keys are deterministic, so this run's own answerable_decisions is
    # the right set to check them against. Checked against answerable_decisions
    # rather than all_decisions -- a question whose statement never became a
    # model in this change (deferred, superseded, dropped) still carries its
    # `question` and `options`, but there is nothing here for an answer to it
    # to change, so it must not validate as an answerable key.
    #
    # The columns check is the third refusal, and it runs in both directions.
    # An option that cannot settle its own key says so with a
    # `columns_prompt` -- "merge on a unique key" is the one that does today
    # -- and it is the only kind that reads `columns`: without them it would
    # write `unique_key=[]`, which leaves the merge nothing to match on, and
    # the report would meanwhile claim rows are matched and updated when every
    # one of them would simply be added. (Not "fails at dbt run time": that
    # was the wording here and on the option itself, and this engine never
    # runs dbt to establish it -- see `merge_option`.) Every other option settles
    # itself, so columns handed to one would be discarded -- silently, and
    # leaving the caller believing a key was recorded somewhere. Read off the
    # option rather than compared against its label, so a consumer rendering
    # these options can ask for the columns without knowing which wording
    # means "needs a key", and a reworded option keeps its requirement.
    if answers:
        answerable = {d.key: {o.label: o for o in d.options} for d in answerable_decisions}
        for key, answer in answers.items():
            if key not in answerable:
                # Three different mistakes land here and the key alone cannot
                # tell them apart: a key nobody ever issued, a key issued for
                # the same script at a different path (the failure this
                # class's docstring warns about), and a question this
                # conversion genuinely carries -- `question` and `options`
                # populated -- whose statement never became a model this run.
                # Only the third can be identified from here, so it is named
                # as itself; the other two are left to the list of keys this
                # run does accept, which is what makes them diagnosable.
                answerable_now = ", ".join(sorted(answerable))
                carried = next(
                    (d for d in all_decisions if d.key == key and d.question and d.options),
                    None,
                )
                accepts = (
                    f"answerable keys: {answerable_now}"
                    if answerable_now
                    else "this run has no answerable questions"
                )
                if carried is not None:
                    raise UnknownAnswerError(
                        f"{key} is a question this conversion carries ({carried.action}), but "
                        "the statement it describes produced no model in this change, so "
                        f"there is nothing for an answer to it to change; {accepts}"
                    )
                raise UnknownAnswerError(
                    f"no question with key {key} in this conversion; {accepts}. A key is "
                    "only valid for a run that read the same SQL at the same path -- see "
                    "UnknownAnswerError"
                )
            chosen_option = answerable[key].get(answer.label)
            if chosen_option is None:
                offered = ", ".join(sorted(answerable[key]))
                raise UnknownAnswerError(
                    f"{key} does not offer {answer.label!r}; it offers {offered}"
                )
            if chosen_option.columns_prompt:
                if not answer.columns:
                    raise UnknownAnswerError(
                        f"{key} was answered {answer.label!r} with no columns; that "
                        f"option needs {chosen_option.columns_prompt}, and cannot be "
                        "applied without them"
                    )
                # The upper bound on those columns -- an option that also
                # asks dbt to check them can only be answered with the one
                # column dbt's built-in test checks -- is enforced in
                # `_incremental_answers` rather than here, because this gate
                # runs after the answers have been applied and that input has
                # no single-column check to apply. Named here because this is
                # where a reader comes looking for the column rules.
            elif answer.columns:
                raise UnknownAnswerError(
                    f"{key} was answered {answer.label!r} with columns "
                    f"{_keys_str(answer.columns)}; that option takes no columns, so "
                    "they would have been discarded"
                )

    return ProjectChange(
        models=tuple(rewritten_models),
        sources=source_entries,
        decisions=all_decisions,
        pending=remaining_pending,
        dialect=state.dialect,
        project_name=ctx.project_name,
        variables=tuple(kept_variables),
        tests=tuple(schema_tests),
    )
