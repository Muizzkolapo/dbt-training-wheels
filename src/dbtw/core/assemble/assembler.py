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
from dbtw.core.passes.types import Decision, ModelDraft, PassState

# Fixed priority used once the role-appropriate layer is missing. "role" itself
# is always tried first by the caller; this is the order the remaining roles
# are tried in, skipping whichever one *was* the role already checked.
_FALLBACK_ROLE_ORDER = ("mart", "staging", "intermediate")


def _decision(kind: str, name: str, action: str, reason: str) -> Decision:
    return Decision(
        key=f"assemble.{kind}.{name}",
        tier=1,
        action=action,
        reason=reason,
        source_file="",
        line_start=0,
        line_end=0,
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
    if layer is None or layer.prefix is None:
        return draft_name, []
    if draft_name.startswith(layer.prefix):
        return draft_name, []
    final_name = f"{layer.prefix}{draft_name}"
    detection = detections.get(f"layer.{layer.name}.prefix")
    evidence = detection.evidence if detection is not None else f"{layer.prefix} prefix"
    action = f"renamed {draft_name} to {final_name} (prefix {layer.prefix!r} — {evidence})"
    reason = f"the {layer.name} layer's models all share the {layer.prefix!r} prefix: {evidence}"
    return final_name, [_decision("rename", draft_name, action, reason)]


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


def _decision_statement_index(dec: Decision) -> int | None:
    """The pipeline statement index embedded in a Decision's key.

    `Decision.key`'s documented shape is "<prefix>.<source_file>:<index>"
    (see the example in `Decision`'s own docstring) -- every `_decision()`
    helper across tier 1 and tier 2 builds it this way. Reading it back out
    here is reading that documented contract, not parsing prose.
    """
    _, _, suffix = dec.key.rpartition(":")
    return int(suffix) if suffix.isdigit() else None


def _find_append_decision_index(
    decisions: tuple[Decision, ...], source_indices: tuple[int, ...]
) -> int | None:
    """Locate the tier-2 Decision recording the append conversion for the
    statement that actually survived into this model.

    Matching on `chosen == "append every row"` alone is not enough: a
    *collision* -- two differently qualified tables sharing a bare name, say
    `staging.orders` beside `mart.orders` -- gets a full `chosen="append
    every row"` Decision recorded for BOTH statements, since `append_pass`
    only skips recording one for a "superseded" verdict. Matching the
    statement index embedded in the Decision's key against
    `AssembledModel.source_indices` (which always names the *surviving*
    statement) is what tells those two Decisions apart; text-matching the
    action against the model's name cannot, since a collision's two
    statements share that name by construction.

    Two INSERTs into the *same* target no longer reach here as a
    redefinition -- `collisions.written_earlier` defers the later one before
    it is ever drafted -- so a collision between different tables is now the
    only way two append Decisions can compete for one model.
    """
    wanted = set(source_indices)
    for i, dec in enumerate(decisions):
        if dec.chosen == "append every row" and _decision_statement_index(dec) in wanted:
            return i
    return None


def _known_projections(
    body: str, dialect: str | None
) -> tuple[list[tuple[str, bool]], bool] | None:
    """The named, non-star output columns a query body projects, as
    (name, was-written-quoted) pairs, plus whether a star projection (`*`
    or `t.*`) is present anywhere in it.

    Works on any `exp.Query` -- a plain `SELECT` or a set operation
    (`UNION`/`INTERSECT`/`EXCEPT`) alike, via `.selects`, which sqlglot's
    own `named_selects` is built on. An unaliased compound projection (a
    bare `CASE` with no `AS`) has no output name at all and is simply
    skipped: it can never match a --unique-key column (which must name a
    real output column), so leaving it out never hides a real match --
    see `_key_status` below, which is the only thing that reads this list.

    None means the body couldn't be parsed as a query at all -- should not
    happen for an append draft's body (always exactly the INSERT's own
    SELECT, re-parsed with the same dialect it was rendered with), but
    callers must describe this honestly rather than folding it into the
    star case, which would claim a construct that was never actually
    there (FINDING 6).
    """
    try:
        node = sqlglot.parse_one(body, read=dialect)
    except SqlglotError:
        return None
    if not isinstance(node, exp.Query):
        return None

    projections: list[tuple[str, bool]] = []
    has_star = False
    for projection in node.selects:
        name = projection.alias_or_name
        if not name:
            continue  # unnamed (e.g. a bare CASE): can never match a key
        if name == "*":
            has_star = True
            continue
        if isinstance(projection, exp.Alias):
            identifier = projection.args.get("alias")
        elif isinstance(projection, exp.Column):
            identifier = projection.this
        else:
            identifier = None
        quoted = bool(isinstance(identifier, exp.Identifier) and identifier.quoted)
        projections.append((name, quoted))
    return projections, has_star


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
    dec: Decision, draft_name: str, keys: tuple[str, ...], *, caveat: str
) -> Decision:
    """Rewrite an append Decision into the merge upgrade `--unique-key` chose.

    `chosen`/`alternatives` mirror the wording `merge_pass` already uses for
    its own script-derived merges ("merge on <keys>" / "append every row"),
    so a model's incremental history reads the same regardless of whether
    the merge came from the script or from this flag. `caveat` is appended
    to `reason` verbatim -- callers own its exact wording, so this function
    never has to guess (and can never fabricate) what's actually true of
    the model's body (FINDING 6).
    """
    keys_str = _keys_str(keys)
    return dataclasses.replace(
        dec,
        action=(
            f"INSERT INTO {draft_name} became an incremental model "
            f"(incremental_strategy='merge', unique_key={list(keys)!r}) — upgraded "
            "from append by --unique-key"
        ),
        reason=(
            "--unique-key was supplied on the command line; an append incremental "
            "re-inserts everything the model selects on every run, so this model "
            "was switched to a merge on the given key instead" + caveat
        ),
        chosen=f"merge on {keys_str}",
        alternatives=(dec.chosen,),
    )


def _apply_unique_key(
    models: list[AssembledModel],
    decisions: tuple[Decision, ...],
    unique_key: tuple[str, ...],
    final_to_draft_name: Mapping[str, str],
    dialect: str | None,
) -> tuple[list[AssembledModel], tuple[Decision, ...], list[Decision]]:
    """Upgrade every eligible append model to merge on `unique_key`.

    A model that is already `merge` keeps its own key untouched -- its ON
    clause is better evidence of the true unique key than a blanket CLI
    flag -- but a Decision is still recorded when the flag disagrees with
    it, so the override that was declined is visible in the report. An
    append model is upgraded only when its own body backs up the key
    (FINDING 2/5/6): every key column must be a real output column of the
    model (matched case-insensitively, per `_key_status`, since a CLI value
    can never be quoted), a star projection gets the key applied with an
    honest caveat instead of a confident match, a genuinely absent column
    blocks the upgrade outright, and a quoted output name that only
    differs by case is left ambiguous rather than guessed either way.
    Naming in every new Decision kind here uses the pre-rename draft name
    -- matching the convention every surrounding tier-2 Decision already
    uses (FINDING 3).
    """
    keys_str = _keys_str(unique_key)
    decisions_list = list(decisions)
    extra_decisions: list[Decision] = []
    new_models: list[AssembledModel] = []
    found_incremental = False

    for model in models:
        if model.incremental_strategy == "append":
            found_incremental = True
            draft_name = final_to_draft_name[model.name]
            known = _known_projections(model.body, dialect)

            if known is None:
                caveat = (
                    " (this model's body could not be parsed to confirm its output "
                    "columns, so this could not be verified)"
                )
            else:
                projections, has_star = known
                # (key, status, matched-or-ambiguous-name) per key column --
                # built as one list, not zip(unique_key, statuses), so the
                # three views below can never drift out of alignment.
                statuses = [(k, *_key_status(k, projections)) for k in unique_key]
                missing = [k for k, status, _ in statuses if status == "missing"]
                ambiguous = [(k, name) for k, status, name in statuses if status == "ambiguous"]
                all_matched = all(status == "matched" for _, status, _ in statuses)

                if missing and not has_star:
                    extra_decisions.append(
                        Decision(
                            key=f"assemble.unique_key_not_selected.{draft_name}",
                            tier=2,
                            action=(
                                f"--unique-key {keys_str} was not applied to "
                                f"{draft_name}: it does not select {_keys_str(tuple(missing))}"
                            ),
                            reason=(
                                "a merge's unique_key must be one of the model's own "
                                "output columns; forcing this key onto a model that "
                                "doesn't select it would fail at dbt run time, so it "
                                "was left as an append incremental instead"
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
                    extra_decisions.append(
                        Decision(
                            key=f"assemble.unique_key_ambiguous.{draft_name}",
                            tier=2,
                            action=(
                                f"--unique-key {keys_str} was not applied to "
                                f"{draft_name}: whether it selects {named} is "
                                "ambiguous, not confirmed"
                            ),
                            reason=(
                                "a quoted output column is case-sensitive, so whether "
                                "it's really the same column as an unquoted "
                                "--unique-key value can't be told from the SQL text "
                                "alone -- the same ambiguous/same/different tri-state "
                                "naming.compare_targets uses for cross-statement "
                                "target identity; left as an append incremental "
                                "rather than guessing either way"
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
                        f"{keys_str} could not be verified)"
                    )
                else:
                    caveat = ""

            index = _find_append_decision_index(decisions, model.source_indices)
            assert index is not None  # every append model has its own append_pass Decision
            decisions_list[index] = _upgrade_to_merge(
                decisions_list[index], draft_name, unique_key, caveat=caveat
            )
            new_models.append(
                dataclasses.replace(model, incremental_strategy="merge", unique_key=unique_key)
            )
        elif model.incremental_strategy == "merge":
            found_incremental = True
            if model.unique_key != unique_key:
                draft_name = final_to_draft_name[model.name]
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
                        source_file="",
                        line_start=0,
                        line_end=0,
                    )
                )
            new_models.append(model)
        else:
            new_models.append(model)

    if not found_incremental:
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
                source_file="",
                line_start=0,
                line_end=0,
            )
        )

    return new_models, tuple(decisions_list), extra_decisions


def assemble(
    state: PassState,
    ctx: ProjectContext,
    *,
    inline_vars: bool = False,
    unique_key: tuple[str, ...] = (),
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

    inherited_decisions = state.decisions
    if unique_key:
        ordered, inherited_decisions, unique_key_decisions = _apply_unique_key(
            ordered, inherited_decisions, unique_key, final_to_draft_name, state.dialect
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
    kept_variables: list[Variable] = []
    # name -> its index in kept_variables, so a later fill-in (below) can
    # replace that entry's default_sql without disturbing its position.
    kept_variable_index: dict[str, int] = {}
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
                kept_index = kept_variable_index.get(variable.name)
                if kept_index is not None:
                    stale = kept_variables[kept_index]
                    kept_variables[kept_index] = Variable(
                        name=stale.name,
                        default_sql=variable.default_sql,
                        source_file=stale.source_file,
                        line_start=stale.line_start,
                    )
            continue
        seen_var_names.add(variable.name)

        if variable.name in declared_var_names:
            # Already declared in the target project's own vars — never
            # inline it, even with --inline-vars: doing so would silently
            # override the project's own declared value with whatever this
            # particular script happened to set locally. Pin the rewrite's
            # variable map to None (a var-only marker) so rewrite_body always
            # renders var(), matching what this Decision actually says
            # (FINDING 6 — Decision and disk must agree).
            variable_defaults[variable.name] = None
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

        variable_defaults[variable.name] = variable.default_sql

        # --inline-vars only actually inlines when there is a literal default
        # to inline. A default-less variable (e.g. `DECLARE @region VARCHAR`
        # with nothing assigned) has no SQL to splice in — rewrite_body
        # falls back to a var() call regardless of inline_vars — so claiming
        # "inlined region's literal default value" would be a lie the body
        # doesn't back up, and skipping change.variables for it would leave
        # the emitted var() call undeclared, breaking `dbt compile` on an
        # undefined var (FINDING 5). Treat it exactly like the keep-as-var
        # case, honestly worded.
        if inline_vars and variable.default_sql is not None:
            chosen = "inline the literal value"
            alternatives = ("keep as a dbt var",)
            action = f"inlined {variable.name}'s literal default value in place of the parameter"
        else:
            chosen = "keep as a dbt var"
            alternatives = ("inline the literal value",)
            if inline_vars:
                action = (
                    f"{variable.name} has no default in the source SQL, so there is no "
                    "literal value to inline; kept as a dbt var instead"
                )
            else:
                action = (
                    f"declared {variable.name} as a dbt var, referenced via var('{variable.name}')"
                )
            kept_variable_index[variable.name] = len(kept_variables)
            kept_variables.append(variable)

        new_decisions.append(
            Decision(
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
                chosen=chosen,
                alternatives=alternatives,
            )
        )

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
            model.body, state.dialect, resolutions_by_key, variable_defaults, inline_vars
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
                alternatives=tuple(sorted({r.ref.name for r in unresolved_resolutions})),
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

    return ProjectChange(
        models=tuple(rewritten_models),
        sources=source_entries,
        decisions=inherited_decisions + tuple(new_decisions),
        pending=remaining_pending,
        dialect=state.dialect,
        project_name=ctx.project_name,
        variables=tuple(kept_variables),
    )
