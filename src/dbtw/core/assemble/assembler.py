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
    Subject,
    append_option,
    inline_option,
    merge_option,
    var_option,
)

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
        if dec.chosen == chosen_label and _decision_statement_index(dec) in wanted:
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
    dec: Decision,
    draft_name: str,
    keys: tuple[str, ...],
    *,
    caveat: str,
    answered_label: str | None,
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
    """
    merge_answer = merge_option(keys)
    if answered_label is not None:
        by = "an answer to this question"
        # The label as the answer sent it, not one re-derived from `keys`:
        # the option that asks for columns is the one that does not name
        # them, so naming it from the key it was given would report a
        # question the user was never asked.
        because = (
            f"this question was answered {answered_label!r}, naming {_keys_str(keys)} as that key"
        )
    else:
        by = "--unique-key"
        because = "--unique-key was supplied on the command line"
    return dataclasses.replace(
        dec,
        action=(
            f"INSERT INTO {draft_name} became an incremental model "
            f"(incremental_strategy='merge', unique_key={list(keys)!r}) — upgraded "
            f"from append by {by}"
        ),
        reason=(
            f"{because}; an append incremental re-inserts everything the model "
            "selects on every run, so this model was switched to a merge on the "
            "given key instead" + caveat
        ),
        chosen=merge_answer.label,
        options=(merge_answer, append_option()),
    )


def _downgrade_to_append(dec: Decision, draft_name: str, keys: tuple[str, ...]) -> Decision:
    """Rewrite a merge Decision into the append an answer chose instead.

    Only an answer can get here: `--unique-key` never turns a merge into an
    append. The script's ON clause is still where `keys` came from, so the
    keyed merge stays on offer as the alternative -- worded with the columns
    it actually named rather than as the keyless "merge on a unique key", so
    the report keeps a record of which key the script proposed and the
    answer turned down.
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
        options=(append_answer, merge_option(keys)),
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
    # The answer took effect exactly when the model came out carrying the key
    # it asked for -- an empty one for "append every row", the named one for a
    # merge.
    if after.unique_key == answered.keys:
        outcome = f"its own question was answered {taken} instead"
        aftermath = "so it was taken and the flag was not applied here"
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
    return Decision(
        key=f"assemble.unique_key_overridden.{draft_name}",
        tier=2,
        action=f"--unique-key {keys_str} was not applied to {draft_name}: {outcome}",
        reason=(
            "--unique-key answers every incremental question in this run at once, "
            "and this model's own question was answered separately; the answer "
            f"naming this one model is the more specific of the two, {aftermath}"
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
) -> tuple[list[AssembledModel], tuple[Decision, ...], list[Decision]]:
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
    """
    keys_str = _keys_str(unique_key)
    decisions_list = list(decisions)
    extra_decisions: list[Decision] = []
    new_models: list[AssembledModel] = []
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
            answered_label = answered.option.label if answered is not None else None
            model_keys_str = _keys_str(model_keys)
            requested = _requested_phrase(model_keys, answered is not None)
            known = _known_projections(model.body, dialect)

            if known is None:
                caveat = (
                    " (this model's body could not be parsed to confirm its output "
                    "columns, so this could not be verified)"
                )
            else:
                projections, has_star = known
                # (key, status, matched-or-ambiguous-name) per key column --
                # built as one list, not zip(model_keys, statuses), so the
                # three views below can never drift out of alignment.
                statuses = [(k, *_key_status(k, projections)) for k in model_keys]
                missing = [k for k, status, _ in statuses if status == "missing"]
                ambiguous = [(k, name) for k, status, name in statuses if status == "ambiguous"]
                all_matched = all(status == "matched" for _, status, _ in statuses)

                if missing and not has_star:
                    extra_decisions.append(
                        Decision(
                            key=f"assemble.unique_key_not_selected.{draft_name}",
                            tier=2,
                            action=(
                                f"{requested} was not applied to "
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
                answered_label=answered_label,
            )
            new_models.append(
                dataclasses.replace(model, incremental_strategy="merge", unique_key=model_keys)
            )
        elif model.incremental_strategy == "merge":
            found_incremental = True
            draft_name = final_to_draft_name[model.name]
            if answered is not None:
                if answered.keys:
                    # The only keyed merge label a merge question ever offers is
                    # its own script-derived one, so an answer that keeps the
                    # merge keeps exactly the key the model already carries.
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
                source_file="",
                line_start=0,
                line_end=0,
            )
        )

    return new_models, tuple(decisions_list), extra_decisions


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
    if unique_key or incremental_answers:
        ordered, inherited_decisions, unique_key_decisions = _apply_unique_key(
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
    # write `unique_key=[]`, which fails at dbt run time, and the report would
    # meanwhile claim a merge that cannot run. Every other option settles
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
    )
