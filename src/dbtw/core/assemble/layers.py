"""Maps a target project's real layers onto staging/intermediate/mart roles."""

from __future__ import annotations

from collections.abc import Mapping

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


def role_for(
    name: str,
    deps: Mapping[str, frozenset[str]],
    dependents: Mapping[str, frozenset[str]],
) -> str:
    if not deps.get(name):
        return "staging"
    if dependents.get(name):
        return "intermediate"
    return "mart"


# The one placement this engine is guessing at, and the two answers to it.
#
# `role_for` reads the dependency graph: a model that reads no model of ours
# is staging, one that other models read is intermediate. Neither is a guess
# -- the first reads only raw tables and the second is demonstrably read by
# something. The third branch is: it reads our models and nothing reads it.
# That looks like the end of the pipeline, and it is equally what an
# intermediate model looks like on the day before the mart that will read it
# is written. Only the reader knows which, so only that fork is asked about.
#
# Asking about every model instead was the first shape of this and was wrong
# twice over: it put a screen in front of placements nothing was unsure of,
# and on a ten-model script it added ten screens to a walk whose whole claim
# is that it asks what matters.
_FORK = ("intermediate", "mart")


def layer_options(
    roles: Mapping[str, LayerInfo | None], derived: str, own: str | None = None
) -> tuple[Option, ...]:
    """The layers a model derived as `derived` could go in, in ROLES order.

    Empty unless `derived` is the guessed one, and one option short of a
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
    """
    if derived != "mart":
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


def _effect(layer: LayerInfo, own: str | None) -> str:
    """What dbt will do with a model placed in `layer`, in dbt's own terms.

    `own` is the materialization the model already carries, and it is why
    this takes an argument rather than reading the layer alone. A layer's
    materialization is its *default*, and a model that names one of its own
    keeps it wherever it is put -- an incremental model moved into a folder
    that defaults to ephemeral is still incremental. Saying otherwise was the
    first shape of this, and it put "materialized as ephemeral" on a button
    that produced an incremental model: an option promising a thing the
    conversion would not do, which is the one sentence on a screen that
    cannot be allowed to be wrong.
    """
    materialization = own or layer.materialization or "the project's default"
    named = f", named with the {layer.prefix} prefix" if layer.prefix else ""
    return f"written to {layer.path}/, materialized as {materialization}{named}"


def intermediate_option(layer: LayerInfo, own: str | None = None) -> Option:
    """Put the model in the project's intermediate layer.

    `own` is the materialization the model already carries, if any; see
    `_effect`.
    """
    return Option(
        label=f"put it in {_where(layer)}",
        effect=_effect(layer, own),
        kind="intermediate",
        plain=(
            "Where the work happens: joins, filters and calculations that other "
            "models read from. Not the table you would point a colleague at."
        ),
    )


def mart_option(layer: LayerInfo, own: str | None = None) -> Option:
    """Put the model in the project's mart layer.

    `own` is the materialization the model already carries, if any; see
    `_effect`.
    """
    return Option(
        label=f"put it in {_where(layer)}",
        effect=_effect(layer, own),
        kind="mart",
        plain=(
            "The table other people query. It is the promise you are making to "
            "them, so it wants a name and a shape you are willing to keep."
        ),
    )


def layer_question(name: str, chosen: str, options: tuple[Option, ...]) -> str:
    """The question to ask about `name`, or "" when there is nothing to ask.

    Empty when the project has fewer than two layers to choose between: a
    question offering one answer is not a question, and putting one on a
    screen teaches a reader that the screens ask things that do not matter.
    """
    if len(options) < 2:
        return ""
    return f"Which layer does {name} belong in?"


def source_option(table: str) -> Option:
    """Declare the table as this project's own source. The default."""
    return Option(
        label=f"declare {table} as my source",
        effect=(
            f"{table} is declared in this project's sources.yml and read with "
            f"source(), so this conversion depends on the raw table"
        ),
        kind="source",
        plain=(
            "Your project names the raw table itself and reads straight from it. "
            "Nothing you build depends on anybody else's work."
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
            f"read with ref('{project}', '{model}') instead of a source, so this "
            f"conversion inherits {project}'s tests and lineage for it -- and its changes"
        ),
        kind="cross_ref",
        plain=(
            "Another team has already tidied this table up in their own project. "
            "You read their finished version instead of the raw one, which means "
            "you get their fixes and also their mistakes."
        ),
    )
