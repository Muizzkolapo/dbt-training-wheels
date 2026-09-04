from dbtw.core.assemble import AssembledModel, SourceEntry
from dbtw.core.emit.render import render_model, render_sources_yaml


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
    )
    base.update(kw)
    return AssembledModel(**base)  # type: ignore[arg-type]


def test_plain_model_is_just_the_body():
    assert render_model(_model()) == "SELECT\n  a\nFROM raw_orders\n"


def test_materialization_renders_a_config_block():
    out = render_model(_model(materialization="table"))
    assert out.startswith("{{ config(\n    materialized='table'\n) }}\n\n")
    assert out.endswith("FROM raw_orders\n")


def test_grants_render_lowercased_and_merged():
    out = render_model(
        _model(
            materialization="table", grants=(("SELECT", ("reporting", "ops")), ("INSERT", ("ops",)))
        )
    )
    assert "materialized='table'," in out
    assert "grants={'insert': ['ops'], 'select': ['reporting', 'ops']}" in out


def test_grants_without_materialization_still_render():
    out = render_model(_model(grants=(("SELECT", ("reporting",)),)))
    assert "{{ config(" in out
    assert "materialized" not in out


def test_leading_comments_render_above_the_config():
    out = render_model(_model(materialization="table", leading_comments=("the orders model",)))
    assert out.startswith("-- the orders model\n\n{{ config(")


def test_file_ends_with_exactly_one_newline():
    out = render_model(_model(materialization="table"))
    assert out.endswith("\n") and not out.endswith("\n\n")


def test_sources_yaml_shape():
    out = render_sources_yaml(
        (
            SourceEntry(source_name="raw", schema="raw", table="orders"),
            SourceEntry(source_name="raw", schema="raw", table="customers"),
            SourceEntry(source_name="crm", schema="crm", table="leads"),
        )
    )
    assert out.startswith("version: 2\n")
    assert "  - name: crm\n" in out
    assert "      - name: customers\n" in out
    assert out.index("name: crm") < out.index("name: raw")  # sources sorted
    assert out.index("customers") < out.index("orders")  # tables sorted


def test_empty_sources_yaml_is_empty_string():
    assert render_sources_yaml(()) == ""
