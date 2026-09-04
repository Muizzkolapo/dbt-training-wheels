import dataclasses

import pytest

from dbtw.core.assemble import AssembledModel, ProjectChange, SourceEntry, TableRef


def test_table_ref_is_immutable_and_keeps_qualification():
    ref = TableRef(catalog="", db="raw", name="orders")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.name = "other"  # type: ignore[misc]
    assert (ref.db, ref.name) == ("raw", "orders")


def test_assembled_model_carries_placement_and_deps():
    model = AssembledModel(
        name="stg_orders",
        path="models/staging/stg_orders.sql",
        body="SELECT 1 AS a",
        materialization=None,
        grants=(),
        layer="staging",
        depends_on=("stg_customers",),
        leading_comments=(),
        source_indices=(0,),
    )
    assert model.materialization is None  # None means "matches the layer default"
    assert model.depends_on == ("stg_customers",)


def test_source_entry_fields():
    entry = SourceEntry(source_name="raw", schema="raw", table="orders")
    assert (entry.source_name, entry.schema, entry.table) == ("raw", "raw", "orders")


def test_project_change_holds_everything_downstream_needs():
    change = ProjectChange(
        models=(),
        sources=(),
        decisions=(),
        pending=(),
        dialect="tsql",
        project_name="jaffle_shop",
    )
    assert change.project_name == "jaffle_shop"
    assert change.dialect == "tsql"
