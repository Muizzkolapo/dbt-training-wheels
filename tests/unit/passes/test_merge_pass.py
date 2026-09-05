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


# --- how a branch spells its target. `_named_column` asserted that every
# target sqlglot produces is a `Column`, on the strength of nothing: a
# row-value `SET (a, b) = (x, y)` gives a `Tuple` and a path assignment gives
# a `Bracket`, and both aborted the whole conversion with a raw AssertionError
# -- no model, no Decision, nothing on the record at all.

MERGE_ROW_VALUE_UPDATE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET (city, email) = (s.city, s.email) "
    "WHEN NOT MATCHED THEN INSERT *"
)


def test_a_row_value_update_set_names_every_column_it_assigns():
    """`SET (city, email) = (s.city, s.email)` is the standard's row-value
    spelling of `SET city = s.city, email = s.email`, and restricts the update
    to the same two columns. sqlglot puts one `Tuple` of columns in the
    assignment's target rather than one column per assignment (probed on
    30.18.0), so reading it needs the Tuple, but the caveat it earns is the
    same one the repeated spelling earns."""
    out = merge_pass(_state((0, _stmt(MERGE_ROW_VALUE_UPDATE))))
    assert out.drafts
    matching = [c for c in _caveats(out) if "merge_update_columns" in c]
    assert len(matching) == 1, _caveats(out)
    assert "city, email" in matching[0]


def test_a_row_value_target_dedupes_against_a_plain_assignment_of_the_same_column():
    """The two spellings name the same columns, so a column assigned by each
    is one column -- reported once, in the order it was first written."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED AND s.hot THEN UPDATE SET (city, email) = (s.city, s.email) "
        "WHEN MATCHED THEN UPDATE SET t.CITY = s.city "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts
    matching = [c for c in _caveats(out) if "merge_update_columns" in c]
    assert len(matching) == 1, _caveats(out)
    assert "city, email" in matching[0]
    assert "CITY" not in matching[0]


def test_a_quoted_column_inside_a_row_value_target_keeps_its_case():
    """The Tuple's members are ordinary columns, so the quoting rule that
    keeps `"City"` distinct from `city` applies inside one too."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        'WHEN MATCHED THEN UPDATE SET ("City", city) = (s.a, s.b) '
        "WHEN NOT MATCHED THEN INSERT *"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts
    matching = [c for c in _caveats(out) if "merge_update_columns" in c]
    assert len(matching) == 1, _caveats(out)
    assert "City, city" in matching[0]


MERGE_PATH_ASSIGNMENT = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.data['city'] = s.city "
    "WHEN NOT MATCHED THEN INSERT *"
)


def test_an_assignment_to_a_path_inside_a_column_is_refused_not_read_as_the_column():
    """`SET t.data['city'] = s.city` writes one key of a semi-structured
    column and leaves the rest of it alone; sqlglot parses that target as a
    `Bracket`, not a `Column` (probed on 30.18.0). dbt's merge assigns whole
    columns only, so calling this an assignment to `data` would overstate what
    the script does while the converted model quietly overwrote the whole
    column on every run. Refused, with the branch quoted verbatim."""
    out = merge_pass(_state((0, _stmt(MERGE_PATH_ASSIGNMENT))))
    assert len(out.pending) == 1
    assert out.drafts == ()
    assert len(out.decisions) == 1, out.decisions
    dec = out.decisions[0]
    assert dec.tier == 2
    assert dec.action.startswith("deferred:")
    assert "t.data['city']" in dec.action
    assert "whole column" in dec.reason


def test_a_path_assignment_and_an_unusable_on_clause_are_both_recorded():
    """Two independent reasons, two Decisions, two distinct keys -- the same
    rule the delete-branch refusal follows."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON 1 = 1 "
        "WHEN MATCHED THEN UPDATE SET t.data['city'] = s.city"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert len(out.decisions) == 2, out.decisions
    assert any("no unique key" in d.action for d in out.decisions)
    assert any("whole column" in d.reason for d in out.decisions)
    assert len({d.key for d in out.decisions}) == 2


def test_a_path_assignment_beside_a_convertible_branch_still_refuses():
    """The unreadable branch must not be lost among siblings dbt can perform,
    exactly as a delete branch is not."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED AND s.hot THEN UPDATE SET t.data['city'] = s.city "
        "WHEN MATCHED THEN UPDATE SET t.city = s.city "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert len(out.pending) == 1
    assert out.drafts == ()
    assert len(out.decisions) == 1, out.decisions
    assert out.decisions[0].action.startswith("deferred:")


# --- how many branches the caveat is talking about. Both column caveats said
# "branch", singular, however many branches contributed a column, so a MERGE
# whose two matched branches assign a column each was described as one branch
# assigning both.

MERGE_TWO_UNMATCHED_BRANCHES = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* "
    "WHEN NOT MATCHED AND s.hot THEN INSERT (id, city) VALUES (s.id, s.city) "
    "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)"
)


def _update_caveat(sql: str) -> str:
    (caveat,) = [
        c for c in _caveats(merge_pass(_state((0, _stmt(sql))))) if "assigns" in c or "assign" in c
    ]
    return caveat


def _insert_caveat(sql: str) -> str:
    (caveat,) = [
        c
        for c in _caveats(merge_pass(_state((0, _stmt(sql)))))
        if "inserts only" in c or "insert only" in c
    ]
    return caveat


def test_the_update_caveat_counts_the_matched_branches_it_describes():
    assert "a WHEN MATCHED branch of dim_c assigns only city, email" in _update_caveat(
        MERGE_BOTH_BRANCHES
    )
    assert (
        "2 WHEN MATCHED branches of dim_c assign only city, email between them"
        in _update_caveat(MERGE_TWO_MATCHED_BRANCHES)
    )


def test_the_insert_caveat_counts_the_unmatched_branches_it_describes():
    assert "a WHEN NOT MATCHED branch of dim_c inserts only id, city" in _insert_caveat(
        MERGE_RESTRICTED_INSERT
    )
    assert (
        "2 WHEN NOT MATCHED branches of dim_c insert only id, city between them"
        in _insert_caveat(MERGE_TWO_UNMATCHED_BRANCHES)
    )


# --- which branches the count is counting. A MERGE can carry several branches
# of the same kind, and only some of them need restrict anything. Counting the
# ones that do not says two false things at once: that there are more
# restricted branches than there are, and that the columns named are all the
# statement ever assigns.

MERGE_STAR_BESIDE_RESTRICTED_UPDATE = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED AND s.hot THEN UPDATE SET t.* = s.* "
    "WHEN MATCHED THEN UPDATE SET t.city = s.city "
    "WHEN NOT MATCHED THEN INSERT *"
)


def test_a_matched_branch_that_assigns_every_column_is_not_counted_as_restricting():
    """One of the two matched branches assigns `t.* = s.*` — every column,
    which is exactly what dbt's merge does and so restricts nothing. Saying
    "2 WHEN MATCHED branches assign only city" would be false twice over."""
    caveat = _update_caveat(MERGE_STAR_BESIDE_RESTRICTED_UPDATE)
    assert "a WHEN MATCHED branch of dim_c assigns only city" in caveat
    assert "2 WHEN MATCHED" not in caveat


MERGE_STAR_BESIDE_RESTRICTED_INSERT = (
    "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.* = s.* "
    "WHEN NOT MATCHED AND s.hot THEN INSERT * "
    "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)"
)


def test_an_unmatched_branch_that_inserts_every_column_is_not_counted_as_restricting():
    """The insert side has the same rule and had the same defect."""
    caveat = _insert_caveat(MERGE_STAR_BESIDE_RESTRICTED_INSERT)
    assert "a WHEN NOT MATCHED branch of dim_c inserts only id" in caveat
    assert "2 WHEN NOT MATCHED" not in caveat


def test_two_restricted_branches_beside_an_unrestricted_one_count_only_the_two():
    """The count is of restricted branches, not of matched branches, and it
    still has to reach two when two of the three restrict."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED AND s.hot THEN UPDATE SET t.* = s.* "
        "WHEN MATCHED AND s.warm THEN UPDATE SET t.city = s.city "
        "WHEN MATCHED THEN UPDATE SET t.email = s.email "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    assert (
        "2 WHEN MATCHED branches of dim_c assign only city, email between them"
        in _update_caveat(sql)
    )


# --- a row value naming exactly one column. `SET (city) = (s.city)` is the
# same restriction as `SET city = s.city`, but sqlglot parses its target as a
# `Paren` rather than a one-member `Tuple` (probed on 30.18.0) -- so the Tuple
# handling alone refused it, and said it was a path assignment while doing so.


def test_a_single_column_row_value_update_names_that_column():
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET (city) = (s.city) "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts
    assert "a WHEN MATCHED branch of dim_c assigns only city" in _update_caveat(sql)


def test_a_single_column_row_value_keeps_its_qualification_and_quoting_rules():
    """The Paren wraps an ordinary column, so the quoted/unquoted rule and the
    `t.` qualification both behave as they do outside one."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        'WHEN MATCHED AND s.hot THEN UPDATE SET (t."City") = (s.a) '
        "WHEN MATCHED THEN UPDATE SET t.city = s.b "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    caveat = _update_caveat(sql)
    assert "City, city" in caveat


# --- an INSERT that supplies no values. sqlglot parses `INSERT DEFAULT
# VALUES` into a target column list holding the DEFAULT keyword (probed on
# 30.18.0), so it was read as a restriction to a column named DEFAULT and the
# report claimed the branch "inserts only DEFAULT" -- a column that does not
# exist, on a model that was written anyway.


def test_an_insert_of_column_defaults_is_refused_not_read_as_a_column_named_default():
    """`INSERT DEFAULT VALUES` writes a row of the target's own column
    defaults and takes nothing from the source. dbt's merge builds an inserted
    row from the model's SELECT, so there is nothing for it to stand in for."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* "
        "WHEN NOT MATCHED THEN INSERT DEFAULT VALUES"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert len(out.pending) == 1
    assert out.drafts == ()
    assert len(out.decisions) == 1, out.decisions
    dec = out.decisions[0]
    assert dec.action.startswith("deferred:")
    assert "inserts only DEFAULT" not in dec.action
    assert "no values" in dec.action.lower()


def test_an_insert_naming_columns_with_no_values_at_all_is_refused_too():
    """sqlglot accepts `INSERT (id, city)` with no VALUES clause, which is not
    a statement any warehouse would run. It reaches the same place as DEFAULT
    VALUES -- a column list with nothing to put in it -- and is refused rather
    than reported as a restriction to id and city."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* "
        "WHEN NOT MATCHED THEN INSERT (id, city)"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert len(out.decisions) == 1, out.decisions
    assert out.decisions[0].action.startswith("deferred:")


# --- what a branch writes INTO its columns, not just which columns it names.
# The column caveats read the target list and stopped there, so a branch could
# write a constant, a default, or a differently named source column and the
# report would say only "inserts only id, city" -- true about the columns,
# silent about the fact that dbt fills them from somewhere else entirely.
# dbt's merge fills every column of an inserted or updated row from the model's
# own SELECT, which is this MERGE's USING source.


def _values_caveat(sql: str) -> str:
    (caveat,) = [
        c for c in _caveats(merge_pass(_state((0, _stmt(sql))))) if "not from the source" in c
    ]
    return caveat


def test_a_branch_that_writes_nothing_from_the_source_is_refused():
    """`VALUES (DEFAULT, DEFAULT)` inserts a row of the target's own column
    defaults, exactly as `INSERT DEFAULT VALUES` does, and reads nothing from
    the source. dbt's merge inserts the row the model selected, so converting
    would write real source rows where the script wrote a placeholder."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* "
        "WHEN NOT MATCHED THEN INSERT (id, city) VALUES (DEFAULT, DEFAULT)"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert len(out.pending) == 1
    assert len(out.decisions) == 1, out.decisions
    assert out.decisions[0].action.startswith("deferred:")
    assert "no value taken from the source" in out.decisions[0].action


def test_a_matched_branch_that_only_sets_a_constant_is_refused_too():
    """The soft-delete shape: `SET t.is_deleted = TRUE` marks a matched row
    without reading the source at all. dbt's merge would overwrite every
    column of that row from the model's SELECT instead -- a different job, not
    a narrower one."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.is_deleted = TRUE "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert len(out.decisions) == 1, out.decisions
    assert "no value taken from the source" in out.decisions[0].action
    assert "t.is_deleted = TRUE" in out.decisions[0].action


def test_a_column_written_from_a_constant_beside_source_columns_is_named():
    """Some of the branch does read the source, so this converts -- but the
    column that doesn't has to be named, because dbt will fill it from the
    model's SELECT rather than leave the constant there."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.city = s.city, t.is_active = FALSE "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts
    caveat = _values_caveat(sql)
    assert "is_active" in caveat
    assert "FALSE" in caveat
    assert "city" not in caveat.split("but the converted model")[0].replace("is_active", "")


def test_a_column_written_from_a_differently_named_source_column_is_named():
    """`SET t.city = s.town` and `SET t.city = s.city` are not the same
    conversion: dbt writes city from the source's own city, so the script's
    town value is silently dropped unless this is on the record."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.id = s.id, t.city = s.town "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    assert merge_pass(_state((0, _stmt(sql)))).drafts
    caveat = _values_caveat(sql)
    assert "city" in caveat
    assert "s.town" in caveat


def test_an_insert_column_written_from_a_default_beside_source_columns_is_named():
    """The insert side of the same rule -- the reported shape, half of it
    reading the source."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* "
        "WHEN NOT MATCHED THEN INSERT (id, city) VALUES (s.id, DEFAULT)"
    )
    assert merge_pass(_state((0, _stmt(sql)))).drafts
    caveat = _values_caveat(sql)
    assert "city" in caveat
    assert "DEFAULT" in caveat


def test_a_column_assigned_from_the_target_itself_is_named():
    """`SET t.city = t.city` keeps the row's existing value; dbt overwrites it
    from the source. A source-qualified column of the same name is the only
    assignment dbt reproduces."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.id = s.id, t.city = t.city "
        "WHEN NOT MATCHED THEN INSERT *"
    )
    assert merge_pass(_state((0, _stmt(sql)))).drafts
    assert "t.city" in _values_caveat(sql)


def test_a_source_column_of_the_same_name_earns_no_values_caveat():
    """The control. `SET t.city = s.CITY` is exactly what dbt's merge does --
    unquoted identifiers fold, so the case difference is not a difference."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.city = s.CITY "
        "WHEN NOT MATCHED THEN INSERT (id, city) VALUES (s.id, s.city)"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts
    assert [c for c in _caveats(out) if "not from the source" in c] == []


def test_an_insert_whose_column_list_and_values_do_not_line_up_is_refused():
    """`INSERT (id, city) VALUES (s.id)` names two columns and supplies one
    value. Nothing can be said about which column gets which value, so nothing
    is said: the branch is refused rather than half-read."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* "
        "WHEN NOT MATCHED THEN INSERT (id, city) VALUES (s.id)"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert len(out.decisions) == 1, out.decisions
    assert out.decisions[0].action.startswith("deferred:")


def test_a_positional_insert_of_defaults_is_refused_even_with_no_column_list():
    """`INSERT VALUES (DEFAULT, DEFAULT)` names no columns, so no column
    caveat is possible -- but whether it reads the source is still knowable,
    and it doesn't."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* "
        "WHEN NOT MATCHED THEN INSERT VALUES (DEFAULT, DEFAULT)"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert "no value taken from the source" in out.decisions[0].action


def test_a_positional_insert_reading_the_source_still_converts():
    """The control for the one above: `INSERT VALUES (s.id, s.city)` names no
    columns either, but it does read the source, so it converts with no
    column caveat to make."""
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* "
        "WHEN NOT MATCHED THEN INSERT VALUES (s.id, s.city)"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts
    assert _caveats(out) == []


# --- the ON clause's own qualifier. Every column comparison in this pass goes
# through naming.same_identifier, but the two lines deciding which side of an
# equality is the target compared the qualifier as a raw string -- so a MERGE
# whose ON clause spells its own alias in a different case was refused with
# "no target-column = source-column equality", of a statement that has one.


def test_the_on_clause_qualifier_matches_the_alias_case_insensitively():
    sql = (
        "MERGE INTO dim_c AS t USING stg_c AS s ON T.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.* = s.* WHEN NOT MATCHED THEN INSERT *"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts, [d.action for d in out.decisions]
    assert out.drafts[0].unique_key == ("id",)


def test_an_unaliased_target_matches_its_own_name_case_insensitively():
    sql = (
        "MERGE INTO dim_c USING stg_c AS s ON DIM_C.id = s.id "
        "WHEN MATCHED THEN UPDATE SET dim_c.* = s.* WHEN NOT MATCHED THEN INSERT *"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts, [d.action for d in out.decisions]
    assert out.drafts[0].unique_key == ("id",)


def test_a_quoted_alias_still_does_not_fold_into_a_different_case():
    """The other half of the rule: a quoted identifier's case is significant,
    so `"T"` and `t` are two different qualifiers and the equality qualifies
    to neither side of the target."""
    sql = (
        'MERGE INTO dim_c AS "T" USING stg_c AS s ON t.id = s.id '
        "WHEN MATCHED THEN UPDATE SET t.* = s.*"
    )
    out = merge_pass(_state((0, _stmt(sql))))
    assert out.drafts == ()
    assert any("no unique key" in d.action for d in out.decisions)
