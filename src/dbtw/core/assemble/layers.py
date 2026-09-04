"""Maps a target project's real layers onto staging/intermediate/mart roles."""

from __future__ import annotations

from collections.abc import Mapping

from dbtw.core.context import LayerInfo, ProjectContext

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
