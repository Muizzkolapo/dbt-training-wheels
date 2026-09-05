from dbtw.core.ingest import ClassifiedStatement, RawStatement
from dbtw.core.passes import Decision, PassState
from dbtw.core.passes.tier2 import merge_pass

MERGE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.n = s.n"
)
MERGE_TWO_KEYS = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.a = s.a AND t.b = s.b "
    "WHEN MATCHED THEN UPDATE SET t.n = s.n"
)
MERGE_NO_KEY = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON 1 = 1 WHEN MATCHED THEN UPDATE SET t.n = s.n"
)


def _stmt(text: str, index: int = 0) -> ClassifiedStatement:
    raw = RawStatement(source_file="t.sql", index=index, text=text, line_start=1, line_end=1)
    return ClassifiedStatement(raw=raw, kind="merge", reason="test")


def _state(*items: tuple[int, ClassifiedStatement]) -> PassState:
    return PassState(pending=tuple(items), drafts=(), decisions=(), dialect=None)


def _conversion(out: PassState) -> Decision:
    """The Decision recording the conversion itself -- the one carrying the
    key-confirmation question. Caveats and refusals never set `chosen`."""
    (dec,) = [d for d in out.decisions if d.chosen]
    return dec


def _caveats(out: PassState) -> list[str]:
    """Each non-conversion Decision's action and reason as one string."""
    return [f"{d.action}\n{d.reason}" for d in out.decisions if not d.chosen]


def test_merge_becomes_an_incremental_model_keyed_on_the_on_clause():
    out = merge_pass(_state((0, _stmt(MERGE))))
    assert out.pending == ()
    (draft,) = out.drafts
    assert draft.name == "dim_c"
    assert draft.materialization == "incremental"
    assert draft.incremental_strategy == "merge"
    assert draft.unique_key == ("id",)
    assert "stg_c" in draft.body


def test_composite_key_keeps_both_columns_in_order():
    out = merge_pass(_state((0, _stmt(MERGE_TWO_KEYS))))
    assert out.drafts[0].unique_key == ("a", "b")


def test_the_decision_asks_whether_the_key_is_unique():
    out = merge_pass(_state((0, _stmt(MERGE))))
    dec = _conversion(out)
    assert dec.tier == 2
    assert dec.question
    assert "id" in dec.chosen
    assert dec.alternatives


def test_merge_without_an_extractable_key_stays_pending():
    out = merge_pass(_state((0, _stmt(MERGE_NO_KEY))))
    assert len(out.pending) == 1
    assert out.drafts == ()
    assert any("no unique key" in d.action for d in out.decisions)


def test_non_merge_kinds_are_untouched():
    raw = RawStatement(source_file="t.sql", index=0, text="SELECT 1", line_start=1, line_end=1)
    sel = ClassifiedStatement(raw=raw, kind="select", reason="t")
    out = merge_pass(PassState(pending=((0, sel),), drafts=(), decisions=(), dialect=None))
    assert out.pending == ((0, sel),)


def test_or_joined_on_clause_is_refused_not_treated_as_a_composite_key():
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s "
        "ON t.id = s.id OR t.legacy_id = s.legacy_id WHEN MATCHED THEN UPDATE SET t.n = s.n"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert len(out.pending) == 1
    assert any("no unique key" in d.action or "disjunct" in d.reason.lower() for d in out.decisions)


def test_source_first_on_clause_still_keys_on_the_target_column():
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON s.src_id = t.id "
        "WHEN MATCHED THEN UPDATE SET t.n = s.n"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts[0].unique_key == ("id",)


def test_unaliased_target_matches_on_its_name():
    sql = (
        "MERGE INTO dim_c USING stg_c AS s ON dim_c.id = s.id WHEN MATCHED THEN UPDATE SET n = s.n"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts[0].unique_key == ("id",)


def test_equality_qualified_to_neither_side_is_excluded():
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON other.x = elsewhere.y "
        "WHEN MATCHED THEN UPDATE SET t.n = s.n"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert any("no unique key" in d.action for d in out.decisions)


# --- WHEN branches. merge_pass read only the target, the ON clause and the
# USING source; nothing read `node.args["whens"]`, so a MERGE's branches were
# discarded without a word while the conversion Decision asserted outright that
# "MERGE's matched/not-matched branches are what dbt's merge incremental
# strategy performs against unique_key" -- false for every MERGE below.

MERGE_BOTH_BRANCHES = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.city = s.city, t.email = s.email "
    "WHEN NOT MATCHED THEN INSERT (id, city, email) VALUES (s.id, s.city, s.email)"
)
MERGE_STAR_UPDATE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* "
    "WHEN NOT MATCHED THEN INSERT *"
)
MERGE_INSERT_ONLY = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *"
)
MERGE_DELETE = "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id WHEN MATCHED THEN DELETE"


def test_matched_only_merge_records_that_dbt_will_insert_rows_the_script_never_did():
    """MERGE has no WHEN NOT MATCHED branch, so the script only ever touches
    rows that already exist. dbt's merge strategy always emits `when not
    matched then insert`, so the converted model inserts rows the script
    never did -- invented behaviour, and it must be on the record."""
    out = merge_pass(_state((0, _stmt(MERGE))))
    assert out.drafts  # a caveat, not a refusal: the conversion still happens
    matching = [c for c in _caveats(out) if "NOT MATCHED" in c and "insert" in c]
    assert len(matching) == 1, _caveats(out)


def test_a_restricted_update_set_records_its_columns_and_the_config_that_restores_it():
    """The script assigns only city and email on a matched row; dbt's merge
    updates every column the model selects, and the body selects *, so every
    other column of a matched row is clobbered on each run. `merge_update_columns`
    is the adapter-specific config that restores the restriction -- named in
    prose, never emitted, since the adapter isn't known at convert time."""
    out = merge_pass(_state((0, _stmt(MERGE_BOTH_BRANCHES))))
    assert out.drafts
    matching = [c for c in _caveats(out) if "merge_update_columns" in c]
    assert len(matching) == 1, _caveats(out)
    caveat = matching[0]
    assert "city" in caveat
    assert "email" in caveat
    assert "every column the model selects" in caveat


def test_a_merge_that_deletes_is_refused_and_stays_pending():
    """dbt's merge strategy has no delete branch at all, so converting a
    MERGE that deletes would drop the whole point of the statement. Same
    shape as the disjunctive-ON refusal: still pending, nothing drafted, one
    tier-2 Decision saying why."""
    out = merge_pass(_state((0, _stmt(MERGE_DELETE))))
    assert len(out.pending) == 1
    assert out.drafts == ()
    assert len(out.decisions) == 1, out.decisions
    dec = out.decisions[0]
    assert dec.tier == 2
    assert dec.action.startswith("deferred:")
    assert "DELETE" in dec.action


def test_a_star_update_with_both_branches_records_no_caveat_at_all():
    """`UPDATE SET t.* = s.*` assigns every column and the NOT MATCHED branch
    inserts, so this MERGE really does what dbt's merge strategy does. A
    caveat here would be a fabrication."""
    out = merge_pass(_state((0, _stmt(MERGE_STAR_UPDATE))))
    assert out.drafts
    assert _caveats(out) == []


def test_insert_only_merge_records_that_dbt_will_update_rows_the_script_left_alone():
    """The mirror of the missing-insert caveat: a MERGE with no WHEN MATCHED
    THEN UPDATE branch never touches an existing row, but dbt's merge updates
    every row matching unique_key."""
    out = merge_pass(_state((0, _stmt(MERGE_INSERT_ONLY))))
    assert out.drafts
    assert len(_caveats(out)) == 1, _caveats(out)
    caveat = _caveats(out)[0]
    assert "MATCHED" in caveat
    assert "update" in caveat.lower()


def test_the_conversion_reason_describes_the_branches_the_statement_actually_has():
    """The old reason asserted a blanket equivalence for every MERGE alike.
    Two MERGEs with different branches must not get the same sentence."""
    matched_only = _conversion(merge_pass(_state((0, _stmt(MERGE)))))
    both = _conversion(merge_pass(_state((0, _stmt(MERGE_BOTH_BRANCHES)))))
    assert matched_only.reason != both.reason
    assert "inserts unmatched rows" in both.reason
    assert "inserts unmatched rows" not in matched_only.reason
    assert "updates matched rows" in matched_only.reason


MERGE_CONDITIONAL = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED AND s.updated_at > t.updated_at THEN UPDATE SET t.* = s.* "
    "WHEN NOT MATCHED THEN INSERT *"
)


def test_a_conditional_when_branch_names_its_condition_verbatim():
    """dbt's merge has no per-branch condition: every row matching unique_key
    is updated, including the ones this MERGE's condition excluded. The
    condition is named as evidence for a human, never applied."""
    out = merge_pass(_state((0, _stmt(MERGE_CONDITIONAL))))
    assert out.drafts
    assert len(_caveats(out)) == 1, _caveats(out)
    assert "s.updated_at > t.updated_at" in _caveats(out)[0]


MERGE_BY_SOURCE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* "
    "WHEN NOT MATCHED BY SOURCE THEN UPDATE SET t.is_deleted = TRUE"
)


def test_a_not_matched_by_source_branch_is_refused_and_stays_pending():
    """A NOT MATCHED BY SOURCE branch acts on target rows the source no longer
    has -- dbt's merge strategy has no such branch, so its action would vanish
    entirely. Refused for the same reason a delete branch is."""
    out = merge_pass(_state((0, _stmt(MERGE_BY_SOURCE))))
    assert len(out.pending) == 1
    assert out.drafts == ()
    assert len(out.decisions) == 1, out.decisions
    dec = out.decisions[0]
    assert dec.action.startswith("deferred:")
    assert "NOT MATCHED BY SOURCE" in dec.action.upper()


MERGE_DO_NOTHING = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id WHEN MATCHED THEN DO NOTHING"
)


def test_a_branch_whose_action_is_neither_update_nor_insert_is_refused():
    """`THEN DO NOTHING` parses to the same `exp.Var` shape a DELETE does.
    dbt's merge updates every matched row, so converting this would act where
    the script deliberately did not."""
    out = merge_pass(_state((0, _stmt(MERGE_DO_NOTHING))))
    assert len(out.pending) == 1
    assert out.drafts == ()
    assert len(out.decisions) == 1, out.decisions
    dec = out.decisions[0]
    assert dec.action.startswith("deferred:")
    assert "DO NOTHING" in dec.action.upper()


def test_a_lowercase_delete_branch_is_refused_too():
    """sqlglot stores a delete branch's action as the source text it read, so
    `then` is `Var(this='delete')` for lowercase SQL -- probed on 30.18.0. A
    case-sensitive match would silently convert this one."""
    sql = "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id when matched then delete"
    out = merge_pass(_state((0, _stmt(sql))))
    assert len(out.pending) == 1
    assert out.drafts == ()


def test_a_delete_branch_alongside_an_update_and_an_insert_still_refuses():
    """A MERGE carries several WHEN branches, and two of them can be matched
    branches with different conditions. Any branch that deletes refuses the
    whole statement -- the delete must not be lost among convertible siblings."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED AND s.stale THEN DELETE "
        "WHEN MATCHED THEN UPDATE SET t.city = s.city "
        "WHEN NOT MATCHED THEN INSERT (id, city) VALUES (s.id, s.city)"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert len(out.pending) == 1
    assert out.drafts == ()
    assert len(out.decisions) == 1, out.decisions
    assert "DELETE" in out.decisions[0].action


def test_a_delete_branch_and_an_unusable_on_clause_are_both_recorded():
    """Two independent reasons this MERGE can't convert. Recording only the
    first would leave the other silent, and the two Decisions must not collide
    on the same key."""
    sql = "MERGE INTO dim_c AS t USING stg_c AS s ON 1 = 1 WHEN MATCHED THEN DELETE"
    out = merge_pass(_state((0, _stmt(sql))))
    assert len(out.pending) == 1
    assert out.drafts == ()
    assert len(out.decisions) == 2
    assert any("DELETE" in d.action for d in out.decisions)
    assert any("no unique key" in d.action for d in out.decisions)
    assert len({d.key for d in out.decisions}) == 2


MERGE_TWO_MATCHED_BRANCHES = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED AND s.hot THEN UPDATE SET t.city = s.city, t.email = s.email "
    "WHEN MATCHED THEN UPDATE SET t.CITY = s.city "
    "WHEN NOT MATCHED THEN INSERT (id, city, email) VALUES (s.id, s.city, s.email)"
)


def test_updated_columns_from_several_branches_dedupe_case_insensitively_in_order():
    """Unquoted identifiers fold case-insensitively in every dialect sqlglot
    supports, so t.CITY and t.city are one column, reported once, in the order
    the assignments were written."""
    out = merge_pass(_state((0, _stmt(MERGE_TWO_MATCHED_BRANCHES))))
    matching = [c for c in _caveats(out) if "merge_update_columns" in c]
    assert len(matching) == 1, _caveats(out)
    caveat = matching[0]
    assert "city, email" in caveat
    assert "CITY" not in caveat


MERGE_QUOTED_COLUMN = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    'WHEN MATCHED THEN UPDATE SET t."City" = s.a, t.city = s.b '
    "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)"
)


def test_a_quoted_updated_column_is_not_folded_into_its_unquoted_spelling():
    """The other half of the same rule: a quoted identifier's case IS
    significant, so "City" and city are two different columns and both must
    be named."""
    out = merge_pass(_state((0, _stmt(MERGE_QUOTED_COLUMN))))
    matching = [c for c in _caveats(out) if "merge_update_columns" in c]
    assert len(matching) == 1, _caveats(out)
    caveat = matching[0]
    assert "City" in caveat
    assert "city" in caveat


# --- the INSERT branch's own column list. Symmetric with the UPDATE SET list:
# the script names the target columns a new row gets, dbt builds the row from
# every column the model selects. Recording one restriction and not the other
# would leave a converted MERGE half-documented.

MERGE_RESTRICTED_INSERT = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* "
    "WHEN NOT MATCHED THEN INSERT (id, city) VALUES (s.id, s.city)"
)


def test_a_restricted_insert_column_list_records_the_columns_it_names():
    """The script sets id and city on a new row and leaves the rest to the
    target's defaults; dbt's merge writes every column the model selects."""
    out = merge_pass(_state((0, _stmt(MERGE_RESTRICTED_INSERT))))
    assert out.drafts
    assert len(_caveats(out)) == 1, _caveats(out)
    caveat = _caveats(out)[0]
    assert "NOT MATCHED" in caveat
    assert "id, city" in caveat


def test_an_insert_branch_with_no_column_list_records_no_column_caveat():
    """`INSERT VALUES (...)` names no target column at all -- it's positional
    over the whole row, so there is no named subset to report. A caveat here
    would be inventing a restriction the script never wrote."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* "
        "WHEN NOT MATCHED THEN INSERT VALUES (s.id, s.city)"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts
    assert _caveats(out) == []


def test_a_restricted_update_and_a_restricted_insert_are_reported_separately():
    """Two different restrictions on two different branches -- one Decision
    each, so neither hides behind the other."""
    out = merge_pass(_state((0, _stmt(MERGE_BOTH_BRANCHES))))
    assert out.drafts
    caveats = _caveats(out)
    assert len(caveats) == 2, caveats
    assert len([c for c in caveats if "WHEN MATCHED" in c and "city, email" in c]) == 1, caveats
    assert len([c for c in caveats if "NOT MATCHED" in c and "id, city, email" in c]) == 1, caveats


def test_a_where_attached_to_a_branch_action_is_named_like_a_branch_condition():
    """sqlglot parses a WHERE after a branch's UPDATE SET onto the action node
    rather than into `When.condition` (probed on 30.18.0) -- it restricts which
    rows the branch touches just the same, and dbt's merge honours neither."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* WHERE t.region = 'EU' "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts
    assert len(_caveats(out)) == 1, _caveats(out)
    assert "t.region = 'EU'" in _caveats(out)[0]
