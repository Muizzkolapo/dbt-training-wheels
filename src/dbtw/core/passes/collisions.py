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

from collections.abc import Sequence

from dbtw.core.naming import compare_keys
from dbtw.core.passes.types import ModelDraft


def written_earlier(
    drafts: tuple[ModelDraft, ...],
    pending_writers: Sequence[tuple[int, tuple[str, str, str]]],
    identity: tuple[str, str, str],
    index: int,
) -> bool:
    """Whether an *earlier* statement in this conversion already writes
    `identity` — in which case a statement that does not define the target's
    whole contents must not draft over it.

    `replace_draft`'s "later definition wins" is the right rule for two
    competing *definitions* of a table — a second `CREATE TABLE ... AS`, or a
    `TRUNCATE`+`INSERT` pair, really does discard whatever preceded it. It is
    the wrong rule for every other pair of statements against one target. A
    `MERGE` followed by an `INSERT`, an `INSERT` followed by a `MERGE`, two
    plain `INSERT`s, an `INSERT` after a rebuild: in each of these both
    statements run and both leave rows behind, so drafting the later one
    alone silently discards the earlier one's whole operation — its key, its
    semantics, its source — under a "redefinition" note that says none of it.

    A dbt model is one SELECT, and combining several statements into one
    would mean inventing a query the script never wrote, so the passes that
    build non-rebuild drafts (`merge_pass`, `append_pass`, and
    `truncate_insert_columns_pass` when its INSERT has no truncate left to
    pair with) defer instead — catalog 2.8. The passes that build a full
    rebuild do not consult this at all, which is what keeps a later
    `CREATE TABLE ... AS` free to supersede.

    Both an already-built draft and a still-pending statement count, because
    pass order is not file order: `merge_pass` runs before `append_pass`, so
    for `INSERT INTO t ...; MERGE INTO t ...` the MERGE is drafted while the
    earlier INSERT is still pending and no draft for it exists yet. Checking
    drafts alone would let the later statement take the model and leave the
    earlier one superseded — the same loss, arrived at through the pipeline's
    own ordering rather than the script's. `pending_writers` is the caller's
    list of `(index, identity)` for every pending statement that would build
    a model for a target.

    File order is the whole point of the `index` comparison. A statement
    *after* the one asking is the opposite situation, and `replace_draft`'s
    existing "superseded" handling is right for it — which is what keeps a
    later `CREATE TABLE ... AS` free to replace everything before it.

    Unlike the checks that *pair* two statements into one model
    (`truncate_insert_pass`, and `append_pass`'s DELETE and TRUNCATE
    lookups), this one is deliberately not scoped to a single source file.
    Those pair, and pairing is a claim about adjacency within one script.
    This one only declines to replace, and replacing a draft destroys what
    the earlier statement wrote whether or not the two share a file — a dbt
    project has one model per name, so two files writing one target collide
    however they were split up. What the caller must not do is describe the
    relationship as one *within* a script; the Decisions say "another
    statement in this conversion".

    Identity is compared with `naming.compare_keys`, and an `"ambiguous"`
    match defers exactly like a confirmed one: two spellings qualified to
    different degrees may or may not be the same table, and guessing wrong
    reinstates the loss this exists to prevent. Only a confirmed
    `"different"` — `staging.events` beside `mart.events` — lets the
    statement through as the standalone model it is.
    """
    if any(
        max(d.source_indices) < index
        and compare_keys(d.identity, identity) in ("same", "ambiguous")
        for d in drafts
    ):
        return True
    return any(
        other < index and compare_keys(other_identity, identity) in ("same", "ambiguous")
        for other, other_identity in pending_writers
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
