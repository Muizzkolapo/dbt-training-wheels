"""Turns model drafts into placed, named, ordered models for a target project."""

from __future__ import annotations

import heapq
from collections.abc import Mapping

from dbtw.core.assemble.layers import layer_roles, role_for
from dbtw.core.assemble.refs import references_in
from dbtw.core.assemble.types import AssembledModel, ProjectChange, SourceEntry, TableRef
from dbtw.core.context import Detection, LayerInfo, ProjectContext
from dbtw.core.passes.types import Decision, ModelDraft, PassState

# Fixed priority used once the role-appropriate layer is missing. "role" itself
# is always tried first by the caller; this is the order the remaining roles
# are tried in, skipping whichever one *was* the role already checked.
_FALLBACK_ROLE_ORDER = ("mart", "staging", "intermediate")


def _qualified(ref: TableRef) -> str:
    """Dotted catalog.db.name, dropping empty parts; bare name if unqualified.

    Mirrors tier1._qualified — the same rule that produces ModelDraft.qualified_name
    — so a schema-qualified reference can be compared against it directly.
    """
    return ".".join(part for part in (ref.catalog, ref.db, ref.name) if part)


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

    Matching a *qualified* reference (one with a db/schema) against a draft or
    an existing model is done by dotted qualified name only, never by bare
    name: a qualified reference can never actually be the CTE-free bare
    ModelInfo entries read off the target project's filesystem — those carry
    no schema information at all — and it can only be a draft in this change
    when that draft's own qualified_name matches exactly. An unqualified
    reference keeps matching by bare name, as before.
    """
    existing_model_names = {m.name for m in ctx.existing_models}
    draft_qualified_names = {d.qualified_name for d in drafts}
    declared = {(s.source_name, s.table): s for s in ctx.existing_sources}

    external: dict[tuple[str, str], TableRef] = {}
    unqualified: set[str] = set()
    for draft in drafts:
        for ref in refs[draft.name]:
            if ref.db:
                if _qualified(ref) in draft_qualified_names:
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


def assemble(state: PassState, ctx: ProjectContext) -> ProjectChange:
    new_decisions: list[Decision] = []
    drafts: tuple[ModelDraft, ...] = state.drafts
    draft_names = {d.name for d in drafts}

    # Step 1
    refs = {d.name: references_in(d.body, state.dialect) for d in drafts}

    # Step 2. A qualified reference (has a db/schema) can only be a dependency
    # on a draft whose own qualified_name matches it exactly — its bare name
    # matching some unrelated draft's bare name is not enough (e.g. a read of
    # raw.orders is not a dependency on a draft that merely happens to be
    # named "orders" while targeting analytics.orders). An unqualified
    # reference keeps matching by bare draft name, as before.
    qualified_to_draft_name = {d.qualified_name: d.name for d in drafts}
    deps: dict[str, frozenset[str]] = {}
    for name in refs:
        dep_names: set[str] = set()
        for r in refs[name]:
            if r.db:
                dep_draft_name = qualified_to_draft_name.get(_qualified(r))
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
    placed: dict[str, AssembledModel] = {}
    # Keyed by draft.name — unique per tier-1's upsert invariant, unlike
    # final_name, which two drafts can share (one gets prefixed into the
    # other's own name; see the dedup loop below). Collecting each draft's
    # own placement Decisions here, instead of appending them to
    # new_decisions immediately, lets a dropped draft's Decisions be
    # discarded wholesale once dropped_names is known — a decision keyed and
    # worded around a model that was never written is not a fix, it's a
    # different bug.
    placement_decisions: dict[str, list[Decision]] = {}

    for draft in drafts:
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

        if layer is not None and draft.materialization == layer.materialization:
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

        placed[draft.name] = AssembledModel(
            name=final_name,
            path=path,
            body=draft.body,
            materialization=materialization,
            grants=draft.grants,
            layer=layer.name if layer is not None else role,
            depends_on=(),  # filled in below, once every draft has a final name
            leading_comments=draft.leading_comments,
            source_indices=draft.source_indices,
        )
        placement_decisions[draft.name] = local_decisions

    # Two drafts can resolve to the same final name (e.g. one gets prefixed
    # into the other's own name). A dbt model is one file, so — before the
    # model list is built — keep only the file-order-later draft (highest
    # max(source_indices), same precedent as tier1's _replace_draft) and
    # record an honest Decision naming both for every draft that's dropped.
    by_final_name: dict[str, list[ModelDraft]] = {}
    for draft in drafts:
        by_final_name.setdefault(final_names[draft.name], []).append(draft)

    dropped_names: set[str] = set()
    for shared_final_name, group in by_final_name.items():
        if len(group) == 1:
            continue
        kept = group[0]
        for candidate in group[1:]:
            if max(candidate.source_indices) >= max(kept.source_indices):
                kept = candidate
        for draft in group:
            if draft is kept:
                continue
            dropped_names.add(draft.name)
            new_decisions.append(
                _decision(
                    "collision",
                    draft.name,
                    f"{draft.name} and {kept.name} both resolve to model "
                    f"{shared_final_name} — kept {kept.name}",
                    "a dbt model is one file; only one definition can survive under "
                    "the same final name — resolve this collision in the source SQL",
                )
            )

    # Only a kept draft's own placement Decisions are real — a dropped
    # draft's file was never written, so its rename/collision/path/
    # materialization Decisions above are discarded; the "both resolve to"
    # Decision just recorded is the only honest record of it.
    for draft in drafts:
        if draft.name in dropped_names:
            continue
        new_decisions.extend(placement_decisions[draft.name])

    # Step 8: translate dependency names to final names now that every draft has one.
    models: list[AssembledModel] = []
    for draft in drafts:
        if draft.name in dropped_names:
            continue
        model = placed[draft.name]
        depends_on = tuple(sorted({final_names[dep] for dep in deps[draft.name]}))
        models.append(
            AssembledModel(
                name=model.name,
                path=model.path,
                body=model.body,
                materialization=model.materialization,
                grants=model.grants,
                layer=model.layer,
                depends_on=depends_on,
                leading_comments=model.leading_comments,
                source_indices=model.source_indices,
            )
        )

    ordered, cycle_decisions = _topological(models)
    new_decisions.extend(cycle_decisions)

    return ProjectChange(
        models=tuple(ordered),
        sources=source_entries,
        decisions=state.decisions + tuple(new_decisions),
        pending=state.pending,
        dialect=state.dialect,
        project_name=ctx.project_name,
    )
