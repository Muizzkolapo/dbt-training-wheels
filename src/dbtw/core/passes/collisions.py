"""The one place a `ModelDraft` is upserted into a pass's running `drafts`
tuple, keyed by unqualified name.

Originally tier-1-only (`tier1._replace_draft`), safe there because tier 1's
two drafting passes (`truncate_insert_pass`, `build_models_pass`) were the
only producers of drafts, always folded in the same order, into the same
tuple. Slice 6b's tier-2 passes (`truncate_insert_columns_pass`, `merge_pass`,
`append_pass`) originally appended with a bare `drafts = (*drafts, draft)`,
bypassing this logic entirely -- safe only while each tier-2 pass ran alone
against an empty draft tuple in isolation. Once folded into one pipeline
behind tier 1 (`runner.run_passes`), a tier-1 draft and a tier-2 draft
resolving to the same bare name would silently coexist as two `ModelDraft`
entries sharing one `.name`, with no Decision ever recording the clash.

That silent coexistence isn't just an unrecorded Decision: `assemble.assembler`
explicitly documents and relies on `draft.name` being unique across the whole
`drafts` tuple ("Keyed by draft.name -- unique per tier-1's upsert invariant").
Under a duplicate name, several of its `{d.name: ... for d in drafts}`
comprehensions silently collapse the two drafts into one dict entry (last
write wins, whichever one happened to iterate last), and its own
same-final-name dedup loop -- keyed by `draft.name` too -- then tests
`draft.name in dropped_names`: since both colliding drafts share that name,
*both* get dropped from the final model list, even though the Decision it
just recorded claims one of them was kept. Routing every drafting pass,
tier 1 and tier 2 alike, through `replace_draft` keeps that invariant true
by construction, so the assembler's downstream logic never sees it broken.
"""

from __future__ import annotations

from dbtw.core.naming import compare_keys
from dbtw.core.passes.types import ModelDraft


def rebuild_draft_for(
    drafts: tuple[ModelDraft, ...], identity: tuple[str, str, str], index: int
) -> ModelDraft | None:
    """The full-rebuild draft an *earlier* statement already built for
    `identity`, if there is one.

    A draft with no `incremental_strategy` was built by a statement that
    defines the target's whole contents — a `TRUNCATE`+`INSERT` pair, or a
    `CREATE TABLE ... AS`. An `INSERT` or `MERGE` into that same target
    *after* it is not a standalone model: the script builds one table across
    several statements, and dbt has no model that adds to itself part-way
    through being built. Drafting the later statement on its own would
    replace the rebuild (`replace_draft` keeps the later definition),
    inverting a `table` model into an `incremental` one and discarding the
    rebuild's own SELECT with only a "redefinition" note to show for it.
    Expressing both at once would mean inventing a combined query the script
    never wrote, so `append_pass` and `merge_pass` defer instead — catalog
    2.8.

    File order is the whole point of the `index` check. A rebuild *after* the
    statement asking is the opposite situation: a TRUNCATE or CREATE wipes
    whatever came before it, so the earlier statement really is superseded,
    and `replace_draft`'s existing "superseded" handling is right for it.

    Identity is compared with `naming.compare_keys`, and an `"ambiguous"`
    match defers exactly like a confirmed one: two spellings qualified to
    different degrees may or may not be the same table, and guessing wrong
    reinstates the inversion this exists to prevent. Only a confirmed
    `"different"` — `staging.events` beside `mart.events` — lets the
    statement through as the standalone model it is.
    """
    return next(
        (
            d
            for d in drafts
            if d.incremental_strategy is None
            and max(d.source_indices) < index
            and compare_keys(d.identity, identity) in ("same", "ambiguous")
        ),
        None,
    )


def replace_draft(
    drafts: tuple[ModelDraft, ...], new: ModelDraft
) -> tuple[tuple[ModelDraft, ...], str | None, ModelDraft | None]:
    """Upsert `new` keyed by unqualified name, honestly resolving collisions.

    Compares file order by the highest source index each draft folds in, so
    whichever definition is later in the file always wins — regardless of
    which pass or which call built it first.

    Identity is `ModelDraft.identity` — `naming.target_key`'s folded triple —
    never `name` or `qualified_name`, which hold the spellings the script
    used. `MERGE INTO EVENTS` and `INSERT INTO Events` name one unquoted
    table, and comparing their spellings made them two drafts: the report
    announced two models, the assembler wrote two files whose names differ
    only in case, and a case-insensitive filesystem kept whichever was
    written last — so one whole conversion, incremental config included,
    disappeared with no Decision recording it. The `identity[2]` name part
    decides which drafts are the same model; the full triple then separates a
    redefinition (same table, defined twice) from a collision (two tables
    whose bare names agree).

    Returns (drafts, verdict, existing):
    - verdict is None when there was no prior draft for this name.
    - "redefinition": same qualified name defined twice; later statement wins.
    - "collision": different qualified names map to the same model name; later wins.
    - "superseded": the existing draft is later in file order; `new` is dropped
      and `drafts` is returned unchanged.
    - `existing` is the prior draft when one was found, else None.
    """
    existing = next((d for d in drafts if d.identity[2] == new.identity[2]), None)
    if existing is None:
        return (*drafts, new), None, None
    if max(existing.source_indices) > max(new.source_indices):
        return drafts, "superseded", existing
    kept = tuple(d for d in drafts if d.identity[2] != new.identity[2])
    verdict = "redefinition" if existing.identity == new.identity else "collision"
    return (*kept, new), verdict, existing
