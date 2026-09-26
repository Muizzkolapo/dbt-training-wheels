"""A query on its own becomes a model named after its file.

The most important fact about dbt is that a model *is* a SELECT in a file,
and the file's name is the model's name. It is also the fact a converter
built around statements that write tables cannot teach, because a query
writes nothing and so has no target to take a name from.

It does have a name: the file it is in. That is not a workaround -- it is
dbt's own rule, and using it means the conversion teaches the rule by
performing it.

Measured against 129 real scripts (`SQL-scripts-master`): 122 of the
statements in them are bare queries, and before this pass every one of those
129 files produced no model, no question and no Decision.
"""

from __future__ import annotations

from tests.unit.passes.helpers import state_from, state_from_files

SELECT = "SELECT user, host FROM mysql.user WHERE account_locked = 'N'\n"


def test_a_query_on_its_own_becomes_a_model_named_after_its_file() -> None:
    state = state_from(SELECT, filename="mysql_users.sql")

    (draft,) = state.drafts

    assert draft.name == "mysql_users"
    assert state.pending == (), "the query was consumed, not left pending"


def test_the_model_is_a_view_because_the_query_stored_nothing() -> None:
    """Running the query by hand stores nothing and recomputes every time,
    and a view is the materialization that keeps that true. A table would
    change what her script does on the way to dbt, which is not a conversion.
    """
    state = state_from(SELECT, filename="mysql_users.sql")

    (draft,) = state.drafts

    assert draft.materialization == "view"


def test_the_body_is_the_query_she_wrote() -> None:
    state = state_from(SELECT, filename="mysql_users.sql")

    (draft,) = state.drafts

    assert "mysql.user" in draft.body
    assert "account_locked" in draft.body
    assert "SELECT" in draft.body


def test_the_naming_rule_is_recorded_rather_than_performed_in_silence() -> None:
    """This Decision is the first thing the tool ever teaches a reader who
    has not used dbt, so it says the rule and not just the outcome.
    """
    state = state_from(SELECT, filename="mysql_users.sql")

    (naming,) = [d for d in state.decisions if "mysql_users" in d.action]

    assert "mysql_users.sql" in naming.reason, "the rule is only a rule if it names the file"
    assert "mysql_users.sql" in naming.plain_reason
    assert naming.plain_reason != naming.reason, "two registers, not one sentence twice"


def test_a_file_with_several_queries_is_not_given_one_name() -> None:
    """A file names one model. Two queries in one file are two models needing
    two names, and only the reader has them -- inventing `mysql_users_2` would
    teach the naming rule wrong on the first try.
    """
    two = "SELECT 1 AS a FROM x;\nSELECT 2 AS b FROM y;\n"

    state = state_from(two, filename="both.sql")

    assert state.drafts == (), "neither query should be named after the file"
    assert len(state.pending) == 2
    said = " ".join(d.action + " " + d.reason for d in state.decisions)
    assert "both.sql" in said


def test_a_file_whose_name_dbt_cannot_use_is_refused_by_name() -> None:
    """dbt names a model after its file, so the file needs a name dbt can
    use. Mangling it would invent a name, and the reader would learn a rule
    that is not the rule.
    """
    state = state_from(SELECT, filename="mysql users (old).sql")

    assert state.drafts == ()
    said = " ".join(d.action + " " + d.reason for d in state.decisions)
    assert "mysql users (old).sql" in said


def test_a_name_another_statement_here_already_builds_is_not_taken_twice() -> None:
    """Two models cannot share a name, and this query is not the other one's
    definition, so folding them together would invent a query the script never
    wrote and dropping one would lose a statement.
    """
    both = (
        "CREATE TABLE revenue_daily AS SELECT 1 AS total FROM raw.orders;\n"
        "SELECT total FROM finance.summary;\n"
    )

    state = state_from(both, filename="revenue_daily.sql")

    names = sorted(d.name for d in state.drafts)
    assert names == ["revenue_daily"], "the CREATE's model stands; the query is not named over it"
    (pending,) = state.pending
    assert pending[1].kind == "select"
    refusal = next(d for d in state.decisions if ".name_taken." in d.key)
    assert "revenue_daily" in refusal.action
    assert refusal.plain_reason


def test_a_name_only_a_tier_two_pass_builds_is_taken_too() -> None:
    """The pass ordering claim, and the reason `select_pass` is not in
    TIER1_PASSES: the drafts it has to check against include every one a
    tier-2 pass will build. Run any earlier and this query would be drafted
    as `revenue_daily` first, and `append_pass`'s own draft would land on it
    through `replace_draft` as a redefinition whose "the last definition wins"
    is false in both directions -- these are not two definitions of one model,
    and one of them would disappear under a note saying so.
    """
    both = (
        "INSERT INTO revenue_daily SELECT 1 AS total FROM raw.orders;\n"
        "SELECT total FROM finance.summary;\n"
    )

    state = state_from(both, filename="revenue_daily.sql")

    assert [d.name for d in state.drafts] == ["revenue_daily"]
    assert [d.key for d in state.decisions if ".redefinition" in d.key] == []
    refusal = next(d for d in state.decisions if ".name_taken." in d.key)
    assert "revenue_daily" in refusal.reason


def test_a_query_is_not_named_after_a_table_it_reads() -> None:
    """dbt resolves ref('orders') to the model, never to the table the model
    was named after, so a model called `orders` reading `orders` reads itself.
    Renaming the file is the reader's call -- and `stg_orders` is the name dbt
    projects give exactly this model, which is worth saying while refusing.
    """
    state = state_from("SELECT id FROM orders\n", filename="orders.sql")

    assert state.drafts == ()
    refusal = next(d for d in state.decisions if ".reads_itself." in d.key)
    assert "stg_orders" in refusal.reason
    assert refusal.plain_reason


def test_a_qualified_table_of_the_same_name_is_not_a_self_read() -> None:
    """`raw.orders` and a model called `orders` are two different things: dbt
    matches a qualified read against a model's own qualified name, so this one
    resolves to a source and the model is built. Refusing it would cost the
    most ordinary conversion there is -- a file named after the table it reads
    from a raw schema.
    """
    state = state_from("SELECT id FROM raw.orders\n", filename="orders.sql")

    (draft,) = state.drafts
    assert draft.name == "orders"
    assert [d.key for d in state.decisions if ".reads_itself." in d.key] == []


def test_a_query_reading_its_own_cte_of_that_name_is_still_built() -> None:
    """A CTE called `orders` is not a table read at all, so it cannot make the
    model read itself -- and a self-read check that counted CTEs would refuse
    a perfectly ordinary query for naming one of its own steps after the file.
    """
    sql = "WITH orders AS (SELECT id FROM raw.o) SELECT id FROM orders\n"

    state = state_from(sql, filename="orders.sql")

    (draft,) = state.drafts
    assert draft.name == "orders"


def test_a_cte_in_a_subquery_does_not_hide_a_self_read() -> None:
    """The self-read check has to ask which CTEs are in scope *where the read
    is written*, not which CTEs the statement contains anywhere.

    Found by review. A CTE named `orders` inside a subquery made the
    top-level `FROM orders` -- a read of the real table sharing the model's
    name -- look like a CTE read, so the check found nothing and the model
    shipped under a success Decision reading itself. That is the exact failure
    the refusal exists to prevent, and it was silent.
    """
    sql = (
        "SELECT id FROM orders\n"
        "UNION ALL\n"
        "SELECT id FROM (WITH orders AS (SELECT 1 AS id) SELECT id FROM orders) AS sub\n"
    )

    state = state_from(sql, filename="orders.sql")

    assert state.drafts == (), "a model named orders would read the orders it selects from"
    refusal = next(d for d in state.decisions if ".reads_itself." in d.key)
    assert "stg_orders" in refusal.reason


def test_a_query_reading_only_its_own_recursive_cte_is_still_built() -> None:
    """The other side of the same scoping rule: a RECURSIVE CTE named after the
    file reads itself by declaration, and no table of that name is read at all.
    Refusing this would cost a legitimate conversion to avoid a self-read that
    is not there.
    """
    sql = (
        "WITH RECURSIVE walk AS (\n"
        "  SELECT 1 AS n UNION ALL SELECT n + 1 AS n FROM walk WHERE n < 10\n"
        ") SELECT n FROM walk\n"
    )

    state = state_from(sql, filename="walk.sql")

    (draft,) = state.drafts
    assert draft.name == "walk"
    assert [d.key for d in state.decisions if ".reads_itself." in d.key] == []


def test_two_files_of_one_name_are_told_they_are_two_files() -> None:
    """The refusal has to say which obstacle it met. Two folders holding an
    `orders.sql` each is not "a statement here already builds orders" and is
    not fixed by moving the query to a file of its own -- it is already in
    one. Only renaming one of the two files helps, and the reader cannot know
    that from a message about a statement.
    """
    state = state_from_files(
        {
            "a/orders.sql": "SELECT id FROM raw.orders\n",
            "b/orders.sql": "SELECT id, total FROM raw.orders_v2\n",
        }
    )

    assert [d.name for d in state.drafts] == ["orders"], "the first file keeps the name"
    refusal = next(d for d in state.decisions if ".name_taken." in d.key)
    assert "a/orders.sql" in refusal.reason, "the two files must be told apart"
    assert "b/orders.sql" in refusal.reason
    assert "Rename either file" in refusal.reason
    assert "file of its own" not in refusal.reason, "it is already in a file of its own"
    assert "two files" in refusal.plain_reason.casefold()


def test_a_name_a_written_table_holds_is_refused_as_a_statement_not_a_file() -> None:
    """The other branch, worded for what it actually met: a table some
    statement in this conversion writes. "Rename either file" would be useless
    advice here -- there is only one file.
    """
    both = (
        "CREATE TABLE revenue_daily AS SELECT 1 AS total FROM raw.orders;\n"
        "SELECT total FROM finance.summary;\n"
    )

    state = state_from(both, filename="revenue_daily.sql")

    refusal = next(d for d in state.decisions if ".name_taken." in d.key)
    assert "a table one of its own statements writes" in refusal.reason
    assert "Rename either file" not in refusal.reason


def test_a_name_differing_only_in_case_is_already_taken() -> None:
    """dbt writes one file per model and names the model after it, so `Orders`
    and `orders` are two model files whose names differ only in case -- one
    file on macOS and on Windows, where whichever is written second is the only
    one kept.

    Found by review. `ModelDraft.identity` deliberately preserves a QUOTED
    name's case, because two quoted SQL identifiers differing only in case are
    two different tables; the lookup here casefolded only the file stem, so a
    quoted `"Orders"` never matched and both drafts were built with no Decision
    recording that one of them was about to be overwritten.
    """
    state = state_from_files(
        {
            "a/create_orders.sql": 'CREATE TABLE "Orders" AS SELECT 1 AS id FROM raw.src;',
            "b/orders.sql": "SELECT id FROM finance.summary\n",
        },
        dialect="postgres",
    )

    assert [d.name for d in state.drafts] == ["Orders"], "the query must not claim the name too"
    refusal = next(d for d in state.decisions if ".name_taken." in d.key)
    assert "a table one of its own statements writes" in refusal.reason


def test_the_refusal_says_the_rule_the_name_actually_broke() -> None:
    """`01_weekly_signups.sql` is how a great many people number a folder of
    scripts, and it was met with "rename the file using letters, numbers and
    underscores only" -- which that filename already obeys. The register a
    reader with no dbt reads has to name the rule they broke, or the only
    thing it tells them is that something is wrong.
    """
    state = state_from(SELECT, filename="01_weekly_signups.sql")

    refusal = next(d for d in state.decisions if ".unusable_name." in d.key)
    assert "cannot start with a number" in refusal.plain_reason
    assert "not starting with a digit" in refusal.reason
