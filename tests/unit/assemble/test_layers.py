from pathlib import Path

from dbtw.core.assemble.layers import layer_roles, role_for
from dbtw.core.context import LayerInfo, ProjectContext, read_project

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _ctx(*layers: LayerInfo) -> ProjectContext:
    return ProjectContext(
        project_name="p",
        model_paths=("models",),
        layers=layers,
        existing_models=(),
        existing_sources=(),
        vars_declared=(),
        detections=(),
    )


def _layer(name: str, path: str) -> LayerInfo:
    return LayerInfo(name=name, path=path, prefix=None, materialization=None)


def test_jaffle_shop_roles_come_from_the_real_project():
    roles = layer_roles(read_project(FIXTURES / "jaffle_shop"))
    assert roles["staging"] is not None
    assert roles["staging"].name == "staging"
    assert roles["staging"].prefix == "stg_"
    assert roles["intermediate"] is None  # jaffle_shop has no intermediate layer
    assert roles["mart"] is not None
    assert roles["mart"].name == "root"  # its marts live at the model-path root


def test_named_mart_layer_wins_over_root():
    roles = layer_roles(_ctx(_layer("root", "models"), _layer("marts", "models/marts")))
    assert roles["mart"] is not None
    assert roles["mart"].name == "marts"


def test_synonyms_are_recognised():
    roles = layer_roles(
        _ctx(
            _layer("base", "models/base"),
            _layer("int", "models/int"),
            _layer("core", "models/core"),
        )
    )
    assert roles["staging"] is not None
    assert roles["intermediate"] is not None
    assert roles["mart"] is not None
    assert roles["staging"].name == "base"
    assert roles["intermediate"].name == "int"
    assert roles["mart"].name == "core"


def test_empty_project_has_no_roles():
    roles = layer_roles(_ctx())
    assert roles == {"staging": None, "intermediate": None, "mart": None}


def test_role_for_source_only_model_is_staging():
    deps = {"a": frozenset(), "b": frozenset({"a"})}
    dependents = {"a": frozenset({"b"}), "b": frozenset()}
    assert role_for("a", deps, dependents) == "staging"


def test_role_for_consumed_middle_model_is_intermediate():
    deps = {"a": frozenset(), "b": frozenset({"a"}), "c": frozenset({"b"})}
    dependents = {"a": frozenset({"b"}), "b": frozenset({"c"}), "c": frozenset()}
    assert role_for("b", deps, dependents) == "intermediate"


def test_role_for_leaf_model_is_mart():
    deps = {"a": frozenset(), "b": frozenset({"a"})}
    dependents = {"a": frozenset({"b"}), "b": frozenset()}
    assert role_for("b", deps, dependents) == "mart"
