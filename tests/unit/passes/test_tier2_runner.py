from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.ingest.types import StatementKind
from dbtw.core.passes import run_passes

MERGE_SQL = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.n = s.n"
)


def _stmt(text: str, kind: StatementKind, file: str = "t.sql") -> ClassifiedStatement:
    raw = RawStatement(source_file=file, index=0, text=text, line_start=1, line_end=1)
    return ClassifiedStatement(raw=raw, kind=kind, reason="test")


def test_tier1_bare_truncate_insert_pair_still_becomes_a_table_model():
    """Tier 1 wins, unchanged: a bare (no column list) TRUNCATE+INSERT pair
    must still become a table model via tier 1's truncate_insert_pass, not
    get reinterpreted as an append by tier 2."""
    stmts = (
        _stmt("TRUNCATE TABLE stg_a", "truncate"),
        _stmt("INSERT INTO stg_a SELECT x FROM raw_a", "insert_select"),
    )
    out = run_passes(stmts, dialect=None)
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "stg_a"
    assert draft.materialization == "table"
    assert draft.incremental_strategy is None
    assert "raw_a" in draft.body
    assert all(d.tier == 1 for d in out.decisions)


def test_column_list_pair_becomes_a_table_model_with_aliases_not_an_append():
    """A column-list TRUNCATE+INSERT pair is tier 2's
    truncate_insert_columns_pass's territory -- it must be consumed before
    append_pass ever sees the INSERT, becoming a table model with positional
    SELECT aliases, never an append incremental."""
    stmts = (
        _stmt("TRUNCATE TABLE stg_b", "truncate"),
        _stmt("INSERT INTO stg_b (a, b) SELECT x, y FROM raw_b", "insert_select"),
    )
    out = run_passes(stmts, dialect=None)
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "stg_b"
    assert draft.materialization == "table"
    assert draft.incremental_strategy is None
    assert "x AS a" in draft.body
    assert "y AS b" in draft.body


def test_merge_becomes_a_merge_incremental_through_the_full_pipeline():
    out = run_passes((_stmt(MERGE_SQL, "merge"),), dialect=None)
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "dim_c"
    assert draft.materialization == "incremental"
    assert draft.incremental_strategy == "merge"
    assert draft.unique_key == ("id",)


def test_bare_insert_becomes_an_append_incremental_through_the_full_pipeline():
    out = run_passes((_stmt("INSERT INTO events SELECT a FROM raw_e", "insert_select"),), None)
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "events"
    assert draft.materialization == "incremental"
    assert draft.incremental_strategy == "append"
    assert draft.unique_key == ()


def test_one_script_with_all_four_shapes_converts_each_correctly():
    """Every tier-1 and tier-2 shape in one script: each converts to its own
    model, and nothing is left pending -- proves TIER1_PASSES and
    TIER2_PASSES fold into one pipeline correctly, in the right order."""
    stmts = (
        _stmt("TRUNCATE TABLE stg_a", "truncate"),
        _stmt("INSERT INTO stg_a SELECT x FROM raw_a", "insert_select"),
        _stmt("TRUNCATE TABLE stg_b", "truncate"),
        _stmt("INSERT INTO stg_b (a, b) SELECT x, y FROM raw_b", "insert_select"),
        _stmt(MERGE_SQL, "merge"),
        _stmt("INSERT INTO events SELECT a FROM raw_e", "insert_select"),
    )
    out = run_passes(stmts, dialect=None)
    assert out.pending == ()
    drafts = {d.name: d for d in out.drafts}
    assert set(drafts) == {"stg_a", "stg_b", "dim_c", "events"}

    assert drafts["stg_a"].materialization == "table"
    assert drafts["stg_a"].incremental_strategy is None

    assert drafts["stg_b"].materialization == "table"
    assert drafts["stg_b"].incremental_strategy is None
    assert "x AS a" in drafts["stg_b"].body
    assert "y AS b" in drafts["stg_b"].body

    assert drafts["dim_c"].materialization == "incremental"
    assert drafts["dim_c"].incremental_strategy == "merge"
    assert drafts["dim_c"].unique_key == ("id",)

    assert drafts["events"].materialization == "incremental"
    assert drafts["events"].incremental_strategy == "append"


# --- Collision hazard: tier-2 draft appends must route through the same
# collision/redefinition/supersede logic tier 1 uses. Before this task, tier
# 2's passes appended drafts with `drafts = (*drafts, draft)`, bypassing
# tier 1's `_replace_draft` upsert entirely -- safe only while each tier-2
# pass ran alone from an empty draft tuple. Folded into one pipeline, a
# tier-1 draft and a tier-2 draft resolving to the same bare name would have
# silently coexisted with no Decision recording the clash, and
# assemble.assembler's own dedup logic (keyed by draft.name, documented as
# relying on tier-1's upsert invariant that draft.name is unique) breaks
# under a duplicate name rather than fixing it: several of its
# `{d.name: ... for d in drafts}` comprehensions silently collapse the two
# drafts into one dict entry, and its own same-final-name dedup loop (also
# keyed by draft.name) then matches on `draft.name in dropped_names` -- since
# both colliding drafts share that name, both get dropped from the final
# model list even though the recorded Decision claims one was kept.


# --- grants_pass ran inside TIER1_PASSES, i.e. before TIER2_PASSES. A GRANT
# on a table that only a tier-2 pass builds (e.g. a MERGE target converted by
# merge_pass) found no matching draft yet and was dropped with "this
# conversion doesn't create it" -- a false claim, since the conversion does
# create it, two passes later.


def test_a_grant_on_a_merge_built_model_attaches_instead_of_being_dropped():
    """Regression: grants_pass ran before tier 2, so it claimed the conversion
    did not create a table that merge_pass creates two passes later."""
    stmts = (
        _stmt(MERGE_SQL, "merge"),
        _stmt("GRANT SELECT ON dim_c TO reporting", "grant"),
    )
    out = run_passes(stmts, dialect=None)
    assert out.pending == ()
    # the false claim: grants_pass ran before merge_pass ever built dim_c
    false_claims = [d.action for d in out.decisions if "doesn't create" in d.action]
    assert false_claims == [], f"grants_pass falsely claimed: {false_claims}"
    (draft,) = out.drafts
    assert draft.name == "dim_c"
    assert draft.materialization == "incremental"  # still built by merge_pass, tier 2
    assert ("SELECT", ("reporting",)) in draft.grants


def test_a_grant_on_a_merge_built_model_keeps_its_incremental_config():
    """Regression: grants_pass replaced the matched draft by reconstructing
    ModelDraft field-by-field, a field list that predates incremental_strategy
    and unique_key -- attaching a GRANT to an incremental model silently
    stripped its incremental config back to "not incremental" (materialization
    still said "incremental", but incremental_strategy went to None and
    unique_key to ()), even though nothing about a GRANT should touch how the
    model is materialized."""
    stmts = (
        _stmt(MERGE_SQL, "merge"),
        _stmt("GRANT SELECT ON dim_c TO reporting", "grant"),
    )
    out = run_passes(stmts, dialect=None)
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "dim_c"
    assert ("SELECT", ("reporting",)) in draft.grants
    assert draft.incremental_strategy == "merge"
    assert draft.unique_key == ("id",)


def test_a_grant_on_a_genuinely_absent_table_still_gets_the_honest_doesnt_create_decision():
    """The fix must not make the "doesn't create" Decision disappear
    entirely -- only stop it from being wrong about tier-2-built tables."""
    stmts = (
        _stmt(MERGE_SQL, "merge"),
        _stmt("GRANT SELECT ON nowhere TO reporting", "grant"),
    )
    out = run_passes(stmts, dialect=None)
    assert out.pending == ()
    drafts = {d.name: d for d in out.drafts}
    assert drafts["dim_c"].grants == ()
    doesnt_create = [d for d in out.decisions if "doesn't create" in d.action]
    assert len(doesnt_create) == 1
    assert "nowhere" not in drafts  # never had a draft to begin with


def test_tier1_and_tier2_name_collision_keeps_one_draft_with_an_honest_decision():
    # staging.events (tier 1 CTAS) and mart.events (tier 2 bare append
    # insert) share the bare name "events" but are different tables.
    stmts = (
        _stmt("CREATE TABLE staging.events AS SELECT 1 AS a", "create_table_as"),
        _stmt("INSERT INTO mart.events SELECT a FROM raw_e", "insert_select"),
    )
    out = run_passes(stmts, dialect=None)
    assert out.pending == ()
    assert len(out.drafts) == 1
    (draft,) = out.drafts
    assert draft.qualified_name == "mart.events"  # later statement in file order wins
    assert draft.incremental_strategy == "append"
    collisions = [d for d in out.decisions if "collision" in d.action]
    assert len(collisions) == 1
    assert "staging.events" in collisions[0].action
    assert "mart.events" in collisions[0].action


def test_an_insert_after_a_tier1_rebuild_of_the_same_table_is_deferred():
    """This used to assert the opposite -- that the later INSERT won and
    became an append, "one dbt model, one file, last wins". That reading is
    wrong for a target the script builds across two statements: the CTAS
    creates the table with `SELECT 1 AS a` in it and the INSERT adds `raw_e`
    to that, so the end state holds both. Converting only the INSERT drops
    the CTAS's rows and inverts a table into an append incremental, with a
    "redefinition" note as the only trace -- the same loss a TRUNCATE+INSERT
    pair suffers when a second INSERT follows it. dbt has no model that adds
    to itself part-way through being built, and combining the two SELECTs
    would invent a query the script never wrote, so the rebuild stands and
    the INSERT is left pending for a human (catalog 2.8)."""
    stmts = (
        _stmt("CREATE TABLE analytics.events AS SELECT 1 AS a", "create_table_as"),
        _stmt("INSERT INTO analytics.events SELECT a FROM raw_e", "insert_select"),
    )
    out = run_passes(stmts, dialect=None)
    assert len(out.drafts) == 1
    (draft,) = out.drafts
    assert draft.incremental_strategy is None
    assert draft.materialization == "table"
    assert "raw_e" not in draft.body
    assert len(out.pending) == 1
    deferrals = [d for d in out.decisions if "rebuilds from scratch" in d.action]
    assert len(deferrals) == 1, [d.action for d in out.decisions]
    assert "analytics.events" in deferrals[0].action or "events" in deferrals[0].action


def test_a_bare_insert_into_a_differently_qualified_rebuild_is_not_deferred():
    """The guard above must not fire on a target that only shares a bare
    name. `staging.events` and `mart.events` both write a schema and disagree
    on it, so they are different tables and the INSERT is the standalone
    append it looks like."""
    stmts = (
        _stmt("CREATE TABLE staging.events AS SELECT 1 AS a", "create_table_as"),
        _stmt("INSERT INTO mart.events SELECT a FROM raw_e", "insert_select"),
    )
    out = run_passes(stmts, dialect=None)
    assert not any("rebuilds from scratch" in d.action for d in out.decisions)
    (draft,) = out.drafts
    assert draft.incremental_strategy == "append"


def test_a_tier1_draft_at_a_later_index_supersedes_an_earlier_tier2_candidate():
    # The earlier-file-order INSERT is bare (no truncate to pair with, no
    # column list) -- append_pass's territory. A LATER full-rebuild CTAS of
    # the same table means the append conversion must not survive: it has to
    # be consumed with an honest "superseded" decision, not silently coexist
    # alongside the CTAS draft or clobber it.
    stmts = (
        _stmt("INSERT INTO analytics.events SELECT a FROM raw_e", "insert_select"),
        _stmt("CREATE TABLE analytics.events AS SELECT 1 AS a", "create_table_as"),
    )
    out = run_passes(stmts, dialect=None)
    assert out.pending == ()
    assert len(out.drafts) == 1
    (draft,) = out.drafts
    assert draft.materialization == "table"
    assert draft.incremental_strategy is None
    assert "raw_e" not in draft.body  # the earlier append candidate's body does not survive
    assert any("superseded" in d.action for d in out.decisions)
