from pathlib import Path

from dbtw.core.assemble import AssembledModel, assemble
from dbtw.core.assemble.assembler import _topological
from dbtw.core.context import read_project
from dbtw.core.passes import ModelDraft, PassState

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _draft(name: str, body: str, materialization: str = "table", **kw) -> ModelDraft:
    return ModelDraft(
        name=name,
        qualified_name=kw.get("qualified_name", name),
        body=body,
        materialization=materialization,
        grants=kw.get("grants", ()),
        source_indices=kw.get("source_indices", (0,)),
        leading_comments=kw.get("leading_comments", ()),
    )


def _state(*drafts: ModelDraft, dialect: str | None = None) -> PassState:
    return PassState(pending=(), drafts=drafts, decisions=(), dialect=dialect)


def _by_name(change):
    return {m.name: m for m in change.models}


def test_source_only_model_goes_to_staging_with_its_prefix():
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(_state(_draft("daily_revenue", "SELECT a FROM raw_orders")), ctx)
    (model,) = change.models
    assert model.name == "stg_daily_revenue"
    assert model.layer == "staging"
    assert model.path == "models/staging/stg_daily_revenue.sql"
    assert any("stg_" in d.action and "daily_revenue" in d.action for d in change.decisions)


def test_prefix_is_not_applied_twice():
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(_state(_draft("stg_orders", "SELECT a FROM raw_orders")), ctx)
    assert change.models[0].name == "stg_orders"


def test_leaf_model_goes_to_the_mart_layer_at_the_project_root():
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(
        _state(
            _draft("base_orders", "SELECT a FROM raw_orders"),
            _draft("revenue", "SELECT a FROM base_orders"),
        ),
        ctx,
    )
    models = _by_name(change)
    assert models["revenue"].layer == "root"
    assert models["revenue"].path == "models/revenue.sql"
    assert models["stg_base_orders"].layer == "staging"


def test_dependencies_use_final_names_and_order_is_topological():
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(
        _state(
            _draft("revenue", "SELECT a FROM base_orders"),
            _draft("base_orders", "SELECT a FROM raw_orders"),
        ),
        ctx,
    )
    order = [m.name for m in change.models]
    assert order.index("stg_base_orders") < order.index("revenue")
    assert _by_name(change)["revenue"].depends_on == ("stg_base_orders",)


def test_materialization_matching_the_layer_default_is_omitted():
    ctx = read_project(FIXTURES / "jaffle_shop")  # staging default is view
    change = assemble(_state(_draft("orders_v", "SELECT a FROM raw_orders", "view")), ctx)
    assert change.models[0].materialization is None
    assert any("omitted" in d.action for d in change.decisions)


def test_materialization_differing_from_the_layer_default_is_kept():
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(_state(_draft("orders_t", "SELECT a FROM raw_orders", "table")), ctx)
    assert change.models[0].materialization == "table"


def test_collision_with_an_existing_model_is_recorded():
    ctx = read_project(FIXTURES / "jaffle_shop")  # already has stg_orders
    change = assemble(_state(_draft("stg_orders", "SELECT a FROM raw_orders")), ctx)
    assert any("already exists" in d.action for d in change.decisions)


def test_missing_layer_falls_back_with_a_decision():
    ctx = read_project(FIXTURES / "no_conventions")  # single root layer only
    change = assemble(_state(_draft("thing", "SELECT a FROM raw_t")), ctx)
    model = change.models[0]
    assert model.path.startswith("models/")
    assert any("no staging layer" in d.reason.lower() for d in change.decisions)


def test_pass_decisions_and_pending_are_carried_through():
    ctx = read_project(FIXTURES / "jaffle_shop")
    state = PassState(pending=(), drafts=(), decisions=(), dialect="tsql")
    change = assemble(state, ctx)
    assert change.dialect == "tsql"
    assert change.project_name == "jaffle_shop"
    assert change.models == ()


def test_dependency_cycle_is_recorded_not_fatal():
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(
        _state(_draft("a_m", "SELECT x FROM b_m"), _draft("b_m", "SELECT x FROM a_m")),
        ctx,
    )
    assert len(change.models) == 2
    assert any("cycle" in d.action.lower() for d in change.decisions)


def test_two_drafts_resolving_to_one_name_keep_the_later_with_an_honest_decision():
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(
        _state(
            _draft("orders_new", "SELECT a FROM raw_orders", source_indices=(0,)),
            _draft("stg_orders_new", "SELECT b FROM raw_orders", source_indices=(5,)),
        ),
        ctx,
    )
    assert [m.name for m in change.models] == ["stg_orders_new"]
    collision = [d for d in change.decisions if "both resolve to" in d.action]
    assert len(collision) == 1
    assert "orders_new" in collision[0].action and "stg_orders_new" in collision[0].action
    assert not any("cycle" in d.action.lower() for d in change.decisions)


def test_dropped_draft_placement_decisions_do_not_duplicate_keys_or_describe_it():
    """orders and stg_orders both resolve to the final name stg_orders (which
    already exists in the target project). Only stg_orders survives; its own
    placement Decisions (collision, materialization) must appear exactly
    once each, and the dropped orders draft must contribute nothing besides
    the honest "both resolve to" Decision that explains the drop.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")  # already has stg_orders
    change = assemble(
        _state(
            _draft("orders", "SELECT a FROM raw_orders", "view", source_indices=(0,)),
            _draft("stg_orders", "SELECT a FROM raw_orders", "view", source_indices=(1,)),
        ),
        ctx,
    )
    assert [m.name for m in change.models] == ["stg_orders"]

    keys = [d.key for d in change.decisions]
    assert len(set(keys)) == len(keys), f"duplicate decision keys: {keys}"

    both_resolve = [d for d in change.decisions if "both resolve to" in d.action]
    assert len(both_resolve) == 1
    assert "orders" in both_resolve[0].action and "stg_orders" in both_resolve[0].action

    for d in change.decisions:
        if d is both_resolve[0]:
            continue
        # No surviving placement Decision may be keyed to the dropped draft's
        # own name — its rename/collision/materialization/path decisions were
        # all for a file that was never written.
        assert d.key != "assemble.materialization.orders"
        assert d.key != "assemble.collision.orders"
        assert d.key != "assemble.path.orders"
        assert d.key != "assemble.rename.orders"


def _model(name: str, depends_on: tuple[str, ...]) -> AssembledModel:
    return AssembledModel(
        name=name,
        path=f"models/{name}.sql",
        body="",
        materialization=None,
        grants=(),
        layer="root",
        depends_on=depends_on,
        leading_comments=(),
        source_indices=(0,),
    )


def test_topological_cycle_decision_names_both_models_and_keeps_both():
    models = [_model("a_m", ("b_m",)), _model("b_m", ("a_m",))]
    ordered, decisions = _topological(models)
    assert {m.name for m in ordered} == {"a_m", "b_m"}
    assert len(decisions) == 1
    assert "a_m" in decisions[0].action and "b_m" in decisions[0].action
    assert "cycle" in decisions[0].action.lower()


def test_topological_duplicate_names_never_produce_a_false_cycle_decision():
    # Defense in depth: assemble() dedupes final-name collisions before ever
    # calling _topological, but the helper itself must not lie if it were
    # ever given two same-named models — no silently-empty "cycle" report.
    models = [_model("dup", ()), _model("dup", ())]
    ordered, decisions = _topological(models)
    assert len(ordered) == 1
    assert decisions == []


def test_qualified_reference_does_not_create_a_false_dependency_on_a_bare_named_draft():
    """A draft targeting analytics.orders and a draft reading raw.orders share
    only a bare name ("orders"); they are different tables and must not be
    linked by a dependency edge.
    """
    ctx = read_project(FIXTURES / "jaffle_shop")
    change = assemble(
        _state(
            _draft("orders", "SELECT 1 AS x", qualified_name="analytics.orders"),
            _draft("stg_raw_orders", "SELECT a FROM raw.orders"),
        ),
        ctx,
    )
    models = _by_name(change)
    assert models["stg_raw_orders"].depends_on == ()


def test_single_non_synonym_layer_is_used_as_a_last_resort(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: reporting_only\nconfig-version: 2\n")
    reporting = tmp_path / "models" / "reporting"
    reporting.mkdir(parents=True)
    (reporting / "rpt_a.sql").write_text("select 1 as id")
    (reporting / "rpt_b.sql").write_text("select 1 as id")
    ctx = read_project(tmp_path)
    change = assemble(_state(_draft("new_thing", "SELECT a FROM raw_t")), ctx)
    model = change.models[0]
    assert model.name == "rpt_new_thing"
    assert model.path == "models/reporting/rpt_new_thing.sql"
