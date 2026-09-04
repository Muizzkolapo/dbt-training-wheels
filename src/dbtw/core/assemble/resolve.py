"""Resolves table references to dbt ref()/source() targets, or an honest unresolved.

Pure name-mapping: no I/O, no sqlglot, no parsing. The qualification rule is the
whole point — a qualified reference (`ref.db` and/or `ref.catalog` non-empty)
carries a schema and/or catalog the author actually wrote, so it is only ever
matched against things that carry the same parts (a draft's `qualified_name`,
built the identical `catalog.db.name` way, or — when a schema is present — a
declared/proposed source's `(schema, table)` key). It never falls back to
bare-name matching against an unqualified draft or an unqualified existing
model, because that bare name might belong to something else entirely in the
target project (the slice-4 Critical: `raw.orders` is not `models/orders.sql`).
An unqualified reference has no schema to look a source up by, so it never
matches a source either.

Two field, not one, carry qualification. `ref.catalog` alone (Snowflake's
`mydb..orders`, parsed as catalog="mydb", db="") is still qualified: it must
never bare-name-match a draft or an existing model, but it also has no schema
to look a source up by, so its only path to resolution is the qualified_name
match; otherwise it is unresolved. Treating `ref.db` as the sole qualification
signal reopens exactly the bug class this module exists to close, just via the
catalog field instead of the schema field.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from dbtw.core.assemble.types import TableRef

ResolutionKind = Literal["ref", "source", "unresolved"]


@dataclass(frozen=True, slots=True)
class Resolution:
    """What a single TableRef resolved to, and why."""

    ref: TableRef
    kind: ResolutionKind
    target: str  # final model name (kind == "ref"); table name (kind == "source"); "" otherwise
    source_name: str  # the source block name (kind == "source" only); "" otherwise
    reason: str  # names the evidence behind the resolution


def _qualified_key(ref: TableRef) -> str:
    """Dotted catalog.db.name, dropping empty parts.

    Mirrors assemble.assembler._qualified / passes.tier1._qualified exactly —
    the same rule that produces ModelDraft.qualified_name — so a qualified
    reference can be compared against it directly. A catalog-only ref (db
    empty) still collapses correctly here: only the non-empty parts join.
    """
    return ".".join(part for part in (ref.catalog, ref.db, ref.name) if part)


def resolve_references(
    refs: tuple[TableRef, ...],
    draft_to_final: Mapping[str, str],
    qualified_to_final: Mapping[str, str],
    existing_models: frozenset[str],
    declared_sources: Mapping[tuple[str, str], str],
    proposed_sources: Mapping[tuple[str, str], str],
) -> tuple[Resolution, ...]:
    return tuple(
        _resolve_one(
            ref,
            draft_to_final=draft_to_final,
            qualified_to_final=qualified_to_final,
            existing_models=existing_models,
            declared_sources=declared_sources,
            proposed_sources=proposed_sources,
        )
        for ref in refs
    )


def _resolve_one(
    ref: TableRef,
    *,
    draft_to_final: Mapping[str, str],
    qualified_to_final: Mapping[str, str],
    existing_models: frozenset[str],
    declared_sources: Mapping[tuple[str, str], str],
    proposed_sources: Mapping[tuple[str, str], str],
) -> Resolution:
    qualified = bool(ref.db) or bool(ref.catalog)

    if qualified:
        key = _qualified_key(ref)
        final = qualified_to_final.get(key)
        if final is not None:
            return Resolution(
                ref=ref,
                kind="ref",
                target=final,
                source_name="",
                reason="matches the model built from this script",
            )

        if not ref.db:
            # Catalog-only (e.g. Snowflake's mydb..orders): qualified, so it
            # never bare-name-matches a draft or existing model, but there is
            # no schema to look a source up by either — its only path to
            # resolution was the qualified_name match above.
            return Resolution(
                ref=ref,
                kind="unresolved",
                target="",
                source_name="",
                reason=f"reference to {key} needs a schema to declare it as a source",
            )

        source_key = (ref.db, ref.name)
        source_name = declared_sources.get(source_key)
        if source_name is not None:
            return Resolution(
                ref=ref,
                kind="source",
                target=ref.name,
                source_name=source_name,
                reason="declared as a source in the target project",
            )
        source_name = proposed_sources.get(source_key)
        if source_name is not None:
            return Resolution(
                ref=ref,
                kind="source",
                target=ref.name,
                source_name=source_name,
                reason="proposed as a source for this change",
            )

        return Resolution(
            ref=ref,
            kind="unresolved",
            target="",
            source_name="",
            reason=f"qualified reference to {key} matches no draft or source",
        )

    final = draft_to_final.get(ref.name)
    if final is not None:
        return Resolution(
            ref=ref,
            kind="ref",
            target=final,
            source_name="",
            reason="matches the model built from this script",
        )

    if ref.name in existing_models:
        return Resolution(
            ref=ref,
            kind="ref",
            target=ref.name,
            source_name="",
            reason="matches an existing model in the target project",
        )

    return Resolution(
        ref=ref,
        kind="unresolved",
        target="",
        source_name="",
        reason=f"unqualified reference to {ref.name} has no schema to match a source by",
    )
