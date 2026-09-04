from dbtw.core.assemble import AssembledModel
from dbtw.core.passes import ModelDraft


def _draft(**kw) -> ModelDraft:
    base = dict(
        name="m",
        qualified_name="m",
        body="SELECT 1 AS a",
        materialization="table",
        grants=(),
        source_indices=(0,),
        leading_comments=(),
    )
    base.update(kw)
    return ModelDraft(**base)  # type: ignore[arg-type]


def test_draft_defaults_to_not_incremental():
    d = _draft()
    assert d.incremental_strategy is None
    assert d.unique_key == ()


def test_draft_carries_strategy_and_key():
    d = _draft(incremental_strategy="merge", unique_key=("id",))
    assert (d.incremental_strategy, d.unique_key) == ("merge", ("id",))


def test_assembled_model_defaults_to_not_incremental():
    m = AssembledModel(
        name="m",
        path="models/m.sql",
        body="SELECT 1 AS a",
        materialization=None,
        grants=(),
        layer="root",
        depends_on=(),
        leading_comments=(),
        source_indices=(0,),
    )
    assert m.incremental_strategy is None
    assert m.unique_key == ()
