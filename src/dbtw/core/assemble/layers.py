"""Maps a target project's real layers onto staging/intermediate/mart roles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from dbtw.core.context import LayerInfo, ProjectContext
from dbtw.core.passes import Option

_STAGING_NAMES = frozenset({"staging", "stg", "base"})
_INTERMEDIATE_NAMES = frozenset({"intermediate", "int"})
_MART_NAMES = frozenset({"marts", "mart", "core", "facts", "dimensions", "dim", "fact"})

ROLES = ("staging", "intermediate", "mart")


def layer_roles(ctx: ProjectContext) -> dict[str, LayerInfo | None]:
    roles: dict[str, LayerInfo | None] = {role: None for role in ROLES}
    for layer in ctx.layers:
        lowered = layer.name.lower()
        if lowered in _STAGING_NAMES and roles["staging"] is None:
            roles["staging"] = layer
        elif lowered in _INTERMEDIATE_NAMES and roles["intermediate"] is None:
            roles["intermediate"] = layer
        elif lowered in _MART_NAMES and roles["mart"] is None:
            roles["mart"] = layer
    if roles["mart"] is None:
        # Real projects (jaffle_shop) keep marts at the model-path root.
        roles["mart"] = next((la for la in ctx.layers if la.name == "root"), None)
    return roles


# Which rule placed a model, as `placement_for` reads the dependency graph.
#
# `one_table`: the model reads no model of ours and at most one table from
# outside this conversion. That is what a staging model is: it tidies one raw
# table, one for one, and joins nothing -- the definition the glossary gives
# the reader. Not a guess.
#
# `read_by_models`: the model reads our models and other models of ours read
# it. Something demonstrably reads it, so it is intermediate. Not a guess.
#
# `end_of_chain`: the model reads our models and nothing reads it. That looks
# like the end of the pipeline, and it is equally what an intermediate model
# looks like on the day before the mart that will read it is written. A guess.
#
# `joins_read` and `joins_unread`: the model reads no model of ours but two or
# more tables from outside this conversion. It combines tables, so it is not a
# one-for-one staging model, whatever else the graph says about its inputs.
# Placing it in staging was the first shape of this, and it put a join into
# the layer that the glossary tells the reader holds no joins. The proposal is
# the layer after staging that the graph supports: intermediate when other
# models read it, mart when nothing does. Both are guesses, because nothing in
# the SQL says whether the combined table is a step or a product.
#
# Asking about every model instead was the first shape of the question and was
# wrong twice over: it put a screen in front of placements nothing was unsure
# of, and on a ten-model script it added ten screens to a walk whose whole
# claim is that it asks what matters.
Rule = Literal["one_table", "read_by_models", "end_of_chain", "joins_read", "joins_unread"]

_GUESSED: frozenset[Rule] = frozenset({"end_of_chain", "joins_read", "joins_unread"})

# The two answers to every guessed placement.
_FORK = ("intermediate", "mart")


@dataclass(frozen=True, slots=True)
class Placement:
    """The role the dependency graph proposes for one model, and the rule
    that proposed it.

    `outside` is how many distinct tables the model reads that this
    conversion does not build, not counting its own target. The reason names
    it, so it is carried rather than counted twice.
    """

    role: str
    rule: Rule
    outside: int

    @property
    def asked(self) -> bool:
        """Whether this placement is a guess that the reader is asked about."""
        return self.rule in _GUESSED


def placement_for(
    name: str,
    deps: Mapping[str, frozenset[str]],
    dependents: Mapping[str, frozenset[str]],
    outside: int,
) -> Placement:
    read_by_ours = bool(dependents.get(name))
    if not deps.get(name):
        if outside < 2:
            return Placement("staging", "one_table", outside)
        if read_by_ours:
            return Placement("intermediate", "joins_read", outside)
        return Placement("mart", "joins_unread", outside)
    if read_by_ours:
        return Placement("intermediate", "read_by_models", outside)
    return Placement("mart", "end_of_chain", outside)


def placement_reason(name: str, placement: Placement) -> str:
    """The rule that placed `name`, in dbt's own terms. Only that rule.

    Listing all three rules was the first shape of this, and it left the
    reader to work out which one applied to the model in front of them.
    """
    graph = "this conversion reads its own dependency graph"
    if placement.rule in ("joins_read", "joins_unread"):
        combined = (
            f"{name} reads {placement.outside} tables that this conversion does not build, "
            "and no model of this conversion. A staging model tidies one raw table and uses "
            f"no join, so {name} is not a staging model"
        )
        if placement.rule == "joins_read":
            proposal = (
                f"Other models of this conversion read {name}, so this conversion proposes "
                "the intermediate layer"
            )
        else:
            proposal = (
                f"No model of this conversion reads {name}, so this conversion proposes the "
                "mart layer"
            )
        return f"{graph}. {combined}. {proposal}"
    _refuse_unasked(name, placement)
    return (
        f"{graph}. {name} reads models of this conversion, and no model of this "
        f"conversion reads {name}. So this conversion proposes the mart layer. An "
        "intermediate model looks the same until the mart that reads it exists"
    )


def _refuse_unasked(name: str, placement: Placement) -> None:
    """Only a guessed placement is asked about, so only a guessed placement
    has a reason to show. The other two rules are facts and record nothing."""
    if not placement.asked:
        raise ValueError(
            f"{name} was placed by the {placement.rule} rule, which asks no question. "
            "Only a guessed placement has a reason to show. This error is a dbtw bug."
        )


def placement_plain(name: str, placement: Placement) -> str:
    """The rule that placed `name`, for a reader who has never used dbt."""
    found = "This tool found the place from what reads what."
    end = f"So {name} looks like the end of the chain, where people query the result."
    middle = f"So {name} looks like a step in the middle of the chain."
    if placement.rule in ("joins_read", "joins_unread"):
        why = (
            f"{name} combines {placement.outside} tables from outside this conversion. A "
            "model that only tidies one table does not combine tables."
        )
        if placement.rule == "joins_read":
            where = f"Other models here read {name}. {middle}"
        else:
            where = f"Nothing here reads {name}. {end}"
    else:
        _refuse_unasked(name, placement)
        why = f"{name} reads other models from this conversion."
        where = f"Nothing here reads {name}. {end}"
    guess = (
        f"That place is a guess about how you will use {name}. You know that better than "
        "this tool does."
    )
    return f"{found} {why} {where} {guess}"


def layer_options(
    roles: Mapping[str, LayerInfo | None], placement: Placement, own: str | None = None
) -> tuple[Option, ...]:
    """The layers a model placed by `placement` could go in, in ROLES order.

    Empty unless the placement is a guess, and one option short of a
    question unless the project has a real layer for both sides of the fork:
    the loop below skips a role with no layer, and `_layer_choice` asks
    nothing when fewer than two options come back. An option offering to put
    a model in a layer the project does not have would promise a placement
    this conversion cannot make -- `_resolve_layer` would fall back somewhere
    else and record a Decision saying so, and the reader would have chosen one
    thing and been given another.

    That used to be a second, explicit check here, and a mutation deleting it
    killed no test: it could not, because the skip and the two-option rule
    already say it. A guard that restates what the code beside it does is one
    more thing to keep true.

    The label names the project's own directory (`marts`, `core`, whatever
    they call it) while the kind stays the role, which is the split `kind`
    exists for: the wording moves with the reader's project and the identity
    does not.

    `own` is the materialization the model carries wherever it is put, or
    None when it takes the default of whichever layer it lands in. See
    `_effect`.
    """
    if not placement.asked:
        return ()
    built = {"intermediate": intermediate_option, "mart": mart_option}
    options: list[Option] = []
    for role in _FORK:
        layer = roles.get(role)
        if layer is None:
            continue
        options.append(built[role](layer, own))
    return tuple(options)


def _where(layer: LayerInfo) -> str:
    """The layer as a reader would go and look at it.

    `root` is what `read_project` calls models sitting at a model-path root
    -- the name of no directory anybody has. Real projects keep their marts
    there (jaffle_shop does), so the path is what gets said.
    """
    return f"{layer.path}/" if layer.name == "root" else layer.name


def _final(layer: LayerInfo, own: str | None) -> str | None:
    """The materialization a model takes in `layer`: its own, or else the
    layer's detected default. None when neither is known."""
    return own or layer.materialization


def _effect(layer: LayerInfo, own: str | None) -> str:
    """What dbt will do with a model placed in `layer`, in dbt's own terms.

    `own` is the materialization the model carries wherever it is put, and it
    is why this takes an argument rather than reading the layer alone. A
    layer's materialization is its *default*, and a model that names one of
    its own keeps it wherever it is put -- an incremental model moved into a
    folder that defaults to ephemeral is still incremental. Saying otherwise
    was the first shape of this, and it put "materialized as ephemeral" on a
    button that produced an incremental model: an option promising a thing the
    conversion would not do, which is the one sentence on a screen that
    cannot be allowed to be wrong.

    The reverse holds too. A model that takes its layer's default (`own` is
    None) takes the default of the layer it lands in, so the option names
    that layer's materialization, not the one where the model was proposed.

    Whole sentences, with a capital and a full stop, because an answer card
    shows this text on its own.
    """
    materialization = _final(layer, own)
    named = f", with the {layer.prefix} prefix in its name" if layer.prefix else ""
    written = f"This conversion writes the model to {layer.path}/{named}."
    if materialization is None:
        return f"{written} The model is materialized as the project's default."
    if materialization == "ephemeral":
        return (
            f"{written} The model is materialized as ephemeral, so dbt builds no relation for it."
        )
    return f"{written} The model is materialized as {materialization}."


# What a reader calls the thing dbt builds for each materialization. An
# incremental model builds a table. Ephemeral builds nothing and is worded on
# its own. A materialization this map does not name (no default detected, or
# a custom one) is "table or view", which is the most that is known.
_BUILDS = {"table": "table", "incremental": "table", "view": "view"}


def intermediate_option(layer: LayerInfo, own: str | None = None) -> Option:
    """Put the model in the project's intermediate layer.

    `own` is the materialization the model already carries, if any; see
    `_effect`. The plain text names what dbt builds for the model there, so
    an ephemeral model is not called a table.
    """
    lead = "A model here does the work that other models read: joins, filters and calculations."
    materialization = _final(layer, own)
    if materialization == "ephemeral":
        plain = (
            f"{lead} dbt builds no table or view for this model. Each model that reads it "
            "runs its SELECT inside its own SQL."
        )
    else:
        builds = _BUILDS.get(materialization or "", "table or view")
        plain = f"{lead} This {builds} is not the one that you would send a colleague to."
    return Option(
        label=f"put it in {_where(layer)}",
        effect=_effect(layer, own),
        kind="intermediate",
        plain=plain,
    )


def mart_option(layer: LayerInfo, own: str | None = None) -> Option:
    """Put the model in the project's mart layer.

    `own` is the materialization the model already carries, if any; see
    `_effect`. An ephemeral model in a mart layer is a model nobody can
    query, and the plain text says so in place of the promise a table makes.
    """
    materialization = _final(layer, own)
    if materialization == "ephemeral":
        plain = (
            "A model here is usually the one that other people query. But dbt builds no "
            "table or view for this model, so nobody can query it. To let people query "
            "it, build it as a view or a table."
        )
    else:
        builds = _BUILDS.get(materialization or "", "table or view")
        plain = (
            f"A model here is the {builds} that other people query. The {builds} is a "
            f"promise that you make to them. So the {builds} needs a name and a shape "
            "that you are willing to keep."
        )
    return Option(
        label=f"put it in {_where(layer)}",
        effect=_effect(layer, own),
        kind="mart",
        plain=plain,
    )


def layer_question(name: str, options: tuple[Option, ...]) -> str:
    """The question to ask about `name`, or "" when there is nothing to ask.

    Empty when the project has fewer than two layers to choose between: a
    question offering one answer is not a question, and putting one on a
    screen teaches a reader that the screens ask things that do not matter.
    """
    if len(options) < 2:
        return ""
    return f"Which layer does {name} belong in?"


def source_option(table: str) -> Option:
    """Declare the table as this project's own source. The default.

    The effect does not say that the project already declares the table: on
    the screen where this is asked, the declaration is a proposal of this
    conversion, and the project may have no sources file yet.
    """
    return Option(
        label=f"declare {table} as my source",
        effect=(
            f"The model reads {table} with source(), as a raw table of this project. So "
            "this conversion depends on the raw table, and on no other project."
        ),
        kind="source",
        plain=(
            "Your project names the raw table itself and reads directly from it. "
            "Nothing that you build depends on the work of another person."
        ),
    )


def cross_ref_option(project: str, model: str) -> Option:
    """Read the table from another project's model instead.

    dbt's two-argument `ref('project', 'model')`. What it buys and what it
    costs are both in `effect`, because they are one fact: their model's
    tests and lineage come with it, and so do their changes.
    """
    return Option(
        label=f"read it from {project}",
        effect=(
            f"The model reads the table with ref('{project}', '{model}'), not with "
            f"source(). This conversion then inherits the tests and lineage of {project} "
            f"for the table. This conversion also inherits the changes that {project} "
            "makes to the table."
        ),
        kind="cross_ref",
        plain=(
            "Another team already cleaned this table in their own project. "
            "You read their finished version instead of the raw table. "
            "So you get their fixes and also their mistakes."
        ),
    )
