"""Rendering incremental_strategy and unique_key into the model config block."""

import dataclasses
from pathlib import Path

from dbtw.core.assemble import AssembledModel, assemble
from dbtw.core.context import read_project
from dbtw.core.emit.render import render_model
from dbtw.core.passes import ModelDraft, PassState

FIXTURES = Path(__file__).parents[2] / "fixtures" / "projects"


def _model(**kw) -> AssembledModel:
    base = dict(
        name="stg_orders",
        path="models/staging/stg_orders.sql",
        body="SELECT\n  a\nFROM raw_orders",
        materialization=None,
        grants=(),
        layer="staging",
        depends_on=(),
        leading_comments=(),
        source_indices=(0,),
        incremental_strategy=None,
        unique_key=(),
    )
    base.update(kw)
    return AssembledModel(**base)  # type: ignore[arg-type]


def test_merge_with_one_key_renders_unique_key_as_a_plain_string():
    out = render_model(
        _model(materialization="incremental", incremental_strategy="merge", unique_key=("id",))
    )
    assert out == (
        "{{ config(\n"
        "    materialized='incremental',\n"
        "    incremental_strategy='merge',\n"
        "    unique_key='id'\n"
        ") }}\n\n"
        "SELECT\n  a\nFROM raw_orders\n"
    )


def test_merge_with_two_keys_renders_unique_key_as_a_list():
    out = render_model(
        _model(
            materialization="incremental",
            incremental_strategy="merge",
            unique_key=("id", "order_date"),
        )
    )
    assert out == (
        "{{ config(\n"
        "    materialized='incremental',\n"
        "    incremental_strategy='merge',\n"
        "    unique_key=['id', 'order_date']\n"
        ") }}\n\n"
        "SELECT\n  a\nFROM raw_orders\n"
    )


def test_append_with_no_key_omits_the_unique_key_line():
    out = render_model(
        _model(materialization="incremental", incremental_strategy="append", unique_key=())
    )
    assert out == (
        "{{ config(\n"
        "    materialized='incremental',\n"
        "    incremental_strategy='append'\n"
        ") }}\n\n"
        "SELECT\n  a\nFROM raw_orders\n"
    )
    assert "unique_key" not in out


def test_grants_render_after_the_incremental_keys():
    out = render_model(
        _model(
            materialization="incremental",
            incremental_strategy="merge",
            unique_key=("id",),
            grants=(("SELECT", ("reporting",)),),
        )
    )
    assert out == (
        "{{ config(\n"
        "    materialized='incremental',\n"
        "    incremental_strategy='merge',\n"
        "    unique_key='id',\n"
        "    grants={'select': ['reporting']}\n"
        ") }}\n\n"
        "SELECT\n  a\nFROM raw_orders\n"
    )


def test_non_incremental_model_renders_exactly_as_before():
    out = render_model(_model(materialization="table", incremental_strategy=None, unique_key=()))
    assert out == "{{ config(\n    materialized='table'\n) }}\n\nSELECT\n  a\nFROM raw_orders\n"


def _draft(name: str, body: str, materialization: str = "table", **kw) -> ModelDraft:
    return ModelDraft(
        name=name,
        qualified_name=kw.get("qualified_name", name),
        body=body,
        materialization=materialization,
        grants=kw.get("grants", ()),
        source_indices=kw.get("source_indices", (0,)),
        leading_comments=kw.get("leading_comments", ()),
        incremental_strategy=kw.get("incremental_strategy"),
        unique_key=kw.get("unique_key", ()),
    )


def _state(*drafts: ModelDraft, dialect: str | None = None) -> PassState:
    return PassState(pending=(), drafts=drafts, decisions=(), dialect=dialect)


def _by_name(change):
    return {m.name: m for m in change.models}


def test_incremental_model_keeps_materialized_even_when_the_layer_default_would_omit_it():
    ctx = read_project(FIXTURES / "jaffle_shop")
    # jaffle_shop's root layer default is "table". Simulate a project where a
    # layer's own configured default happens to be "incremental" -- the
    # invariant under test is that an incremental draft's materialized config
    # is never omitted as a layer default, no matter what that default is,
    # because incremental_strategy/unique_key still need materialized='incremental'
    # alongside them to mean anything.
    layers = tuple(
        dataclasses.replace(layer, materialization="incremental") if layer.name == "root" else layer
        for layer in ctx.layers
    )
    ctx = dataclasses.replace(ctx, layers=layers)
    change = assemble(
        _state(
            _draft(
                "revenue",
                "SELECT a FROM base_orders",
                materialization="incremental",
                incremental_strategy="append",
            ),
            _draft("base_orders", "SELECT a FROM raw_orders"),
        ),
        ctx,
    )
    model = _by_name(change)["revenue"]
    assert model.materialization == "incremental"
    assert not any("omitted" in d.action for d in change.decisions)
    out = render_model(model)
    assert "materialized='incremental'" in out
