"""Tier-1 (mechanical) passes: certain conversions, applied without asking.

Each pass is a pure function PassState -> PassState. Consumed statements
always leave a Decision; unhandled statements stay pending for later tiers.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import PurePath

import sqlglot
from sqlglot import exp

from dbtw.core.ingest.types import ClassifiedStatement
from dbtw.core.naming import (
    compare_keys,
    compare_targets,
    is_external_read,
    qualified_name,
    target_key,
)
from dbtw.core.passes.collisions import replace_draft
from dbtw.core.passes.types import Decision, ModelDraft, PassState


def _parse(stmt: ClassifiedStatement, dialect: str | None) -> exp.Expr:
    return sqlglot.parse_one(stmt.raw.text, read=dialect)


def _as_table(obj: object) -> exp.Table | None:
    if isinstance(obj, exp.Table):
        return obj
    if isinstance(obj, exp.Schema) and isinstance(obj.this, exp.Table):
        return obj.this
    return None


def _target_of(node: exp.Expr) -> exp.Table | None:
    if isinstance(node, exp.Create | exp.Insert):
        return _as_table(node.this)
    if isinstance(node, exp.TruncateTable):
        return _as_table(node.expressions[0]) if node.expressions else None
    if isinstance(node, exp.Select):
        into = node.args.get("into")
        return _as_table(into.this) if into is not None else None
    if isinstance(node, exp.Grant):
        return _as_table(node.args.get("securable"))
    return None


def _decision(
    stmt: ClassifiedStatement,
    index: int,
    name: str,
    action: str,
    reason: str,
    plain_reason: str = "",
) -> Decision:
    return Decision(
        key=f"tier1.{name}.{stmt.raw.source_file}:{index}",
        tier=1,
        action=action,
        reason=reason,
        plain_reason=plain_reason,
        source_file=stmt.raw.source_file,
        line_start=stmt.raw.line_start,
        line_end=stmt.raw.line_end,
    )


def build_models_pass(state: PassState) -> PassState:
    pending: list[tuple[int, ClassifiedStatement]] = []
    drafts = state.drafts
    decisions = list(state.decisions)
    for index, stmt in state.pending:
        if stmt.kind not in ("create_table_as", "create_view"):
            pending.append((index, stmt))
            continue
        node = _parse(stmt, state.dialect)
        table = _target_of(node)
        if table is None:
            pending.append((index, stmt))
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "build",
                    action="left as-is: couldn't determine the target table name",
                    reason="a model needs a name; the target isn't a plain table reference",
                )
            )
            continue
        if isinstance(node, exp.Select):
            body_node = node.copy()
            body_node.set("into", None)
            body = body_node.sql(dialect=state.dialect, pretty=True)
        else:
            body = node.expression.sql(dialect=state.dialect, pretty=True)
        materialization = "view" if stmt.kind == "create_view" else "table"
        draft = ModelDraft(
            name=table.name,
            qualified_name=qualified_name(table),
            identity=target_key(table),
            body=body,
            materialization=materialization,
            grants=(),
            source_indices=(index,),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
        )
        drafts, verdict, existing = replace_draft(drafts, draft)
        if verdict == "superseded":
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "build.supersede",
                    action=(
                        f"superseded: this earlier definition of {table.name} is replaced "
                        "by a later full rebuild"
                    ),
                    reason=(
                        "a later statement in this conversion fully rebuilds this table; dbt keeps "
                        "only the final definition"
                    ),
                )
            )
            continue
        if verdict == "redefinition":
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "build.redefinition",
                    action=f"redefinition of {table.name} — kept the last definition",
                    reason="defined twice; a dbt model is one file, so the last definition wins",
                )
            )
        elif verdict == "collision":
            assert existing is not None  # a collision always has a prior draft
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "build.collision",
                    action=(
                        f"{existing.qualified_name} and {draft.qualified_name} both map to "
                        f"model {table.name} — kept {draft.qualified_name}; resolve the "
                        "collision before deploying"
                    ),
                    reason=(
                        "two different source tables produce the same model name; dbt needs "
                        "one definition per model"
                    ),
                )
            )
        decisions.append(
            _decision(
                stmt,
                index,
                "build",
                action=f"created model {table.name} (materialized='{materialization}')",
                reason="dbt models are SELECTs; the CREATE wrapper becomes materialization config",
            )
        )
    return PassState(
        pending=tuple(pending), drafts=drafts, decisions=tuple(decisions), dialect=state.dialect
    )


def truncate_insert_pass(state: PassState) -> PassState:
    pending = list(state.pending)
    drafts = state.drafts
    decisions = list(state.decisions)
    consumed: set[int] = set()
    # Keyed by naming.target_key, not by the target's raw spelling: unquoted
    # identifiers fold, so `TRUNCATE TABLE Rebuild_t` pairs with `INSERT INTO
    # rebuild_t`. A key miss means "no confirmed pair", never "a different
    # table" — a target qualified to a different degree is ambiguous, and
    # append_pass refuses to convert an INSERT with an ambiguous truncate
    # rather than treat the miss as licence.
    truncates: dict[tuple[str, tuple[str, str, str]], tuple[int, ClassifiedStatement]] = {}
    for index, stmt in pending:
        if stmt.kind == "truncate":
            node = _parse(stmt, state.dialect)
            table = _target_of(node)
            if table is not None:
                key = (stmt.raw.source_file, target_key(table))
                truncates[key] = (index, stmt)
            continue
        if stmt.kind != "insert_select":
            continue
        node = _parse(stmt, state.dialect)
        table = _target_of(node)
        if table is None:
            continue
        key = (stmt.raw.source_file, target_key(table))
        pair = truncates.get(key)
        if pair is None or pair[0] > index:
            continue
        if isinstance(node.this, exp.Schema):
            decisions.append(
                Decision(
                    key=f"tier1.truncate_insert.{stmt.raw.source_file}:{index}",
                    tier=2,
                    action=(
                        f"deferred: TRUNCATE+INSERT into {table.name} has an explicit column list"
                    ),
                    reason=("column-to-column mapping is a Tier 2 decision; left for that pass"),
                    source_file=stmt.raw.source_file,
                    line_start=stmt.raw.line_start,
                    line_end=stmt.raw.line_end,
                )
            )
            del truncates[key]
            continue
        body = node.expression.sql(dialect=state.dialect, pretty=True)
        draft = ModelDraft(
            name=table.name,
            qualified_name=qualified_name(table),
            identity=target_key(table),
            body=body,
            materialization="table",
            grants=(),
            source_indices=(pair[0], index),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
        )
        drafts, verdict, existing = replace_draft(drafts, draft)
        # Statements within a single call are processed in ascending file
        # order, and each table's truncates entry is deleted once paired, so
        # a later pair here can never be superseded by an earlier one.
        assert verdict != "superseded", "unreachable: pairs form in ascending file order"
        consumed.update({pair[0], index})
        del truncates[key]
        if verdict == "redefinition":
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "truncate_insert.redefinition",
                    action=f"redefinition of {table.name} — kept the last definition",
                    reason="defined twice; a dbt model is one file, so the last definition wins",
                )
            )
        elif verdict == "collision":
            assert existing is not None  # a collision always has a prior draft
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "truncate_insert.collision",
                    action=(
                        f"{existing.qualified_name} and {draft.qualified_name} both map to "
                        f"model {table.name} — kept {draft.qualified_name}; resolve the "
                        "collision before deploying"
                    ),
                    reason=(
                        "two different source tables produce the same model name; dbt needs "
                        "one definition per model"
                    ),
                )
            )
        decisions.append(
            _decision(
                stmt,
                index,
                "truncate_insert",
                action=(
                    f"TRUNCATE + INSERT INTO {table.name} became one model (materialized='table')"
                ),
                reason="truncate-then-insert is a full rebuild, like dbt's table materialization",
            )
        )
    return PassState(
        pending=tuple((i, s) for i, s in pending if i not in consumed),
        drafts=drafts,
        decisions=tuple(decisions),
        dialect=state.dialect,
    )


def grants_pass(state: PassState) -> PassState:
    pending: list[tuple[int, ClassifiedStatement]] = []
    drafts = list(state.drafts)
    decisions = list(state.decisions)
    for index, stmt in state.pending:
        if stmt.kind != "grant":
            pending.append((index, stmt))
            continue
        node = _parse(stmt, state.dialect)
        if isinstance(node, exp.Revoke):
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "grants",
                    action="dropped: REVOKE has no dbt equivalent",
                    reason=(
                        "dbt's grants config is declarative — each run applies exactly "
                        "the listed grants, so revocation is expressed by omission"
                    ),
                )
            )
            continue
        table = _target_of(node)
        privileges = tuple(p.sql(dialect=state.dialect) for p in node.args.get("privileges") or ())
        principals = tuple(p.sql(dialect=state.dialect) for p in node.args.get("principals") or ())
        # Matched on naming.target_key's folded name, not on raw text: a
        # GRANT spelling its table `orders` names the model `CREATE TABLE
        # Orders` just built, and dropping it as "an object this conversion
        # doesn't create" contradicts the model file written in the same run.
        grant_identity = None if table is None else target_key(table)
        match = next(
            (
                i
                for i, d in enumerate(drafts)
                if grant_identity is not None and d.identity[2] == grant_identity[2]
            ),
            None,
        )
        if match is None:
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "grants",
                    action=(
                        "dropped with note: GRANT references an object "
                        "this conversion doesn't create"
                    ),
                    reason=(
                        "dbt grants attach to a model's config; there is "
                        "no model here to attach them to"
                    ),
                )
            )
            continue
        d = drafts[match]
        new_grants = d.grants + tuple((priv, principals) for priv in privileges)
        drafts[match] = dataclasses.replace(
            d, grants=new_grants, folded_indices=d.folded_indices + (index,)
        )
        decisions.append(
            _decision(
                stmt,
                index,
                "grants",
                action=f"attached grants to model {d.name}",
                reason=(
                    "GRANT statements become the model's grants config, "
                    "applied by dbt after each build"
                ),
            )
        )
    return PassState(
        pending=tuple(pending),
        drafts=tuple(drafts),
        decisions=tuple(decisions),
        dialect=state.dialect,
    )


def drop_session_pass(state: PassState) -> PassState:
    pending: list[tuple[int, ClassifiedStatement]] = []
    decisions = list(state.decisions)
    for index, stmt in state.pending:
        if stmt.kind != "session":
            pending.append((index, stmt))
            continue
        decisions.append(
            _decision(
                stmt,
                index,
                "session",
                action=f"dropped session statement: {stmt.raw.text.splitlines()[-1][:60]}",
                reason="connection and session state live in profiles.yml, not in models",
            )
        )
    return PassState(
        pending=tuple(pending),
        drafts=state.drafts,
        decisions=tuple(decisions),
        dialect=state.dialect,
    )


def drop_ddl_pass(state: PassState) -> PassState:
    pending: list[tuple[int, ClassifiedStatement]] = []
    decisions = list(state.decisions)
    inserts: list[tuple[str, exp.Table]] = []
    for _, stmt in state.pending:
        if stmt.kind != "insert_select":
            continue
        table = _target_of(_parse(stmt, state.dialect))
        if table is not None:
            inserts.append((stmt.raw.source_file, table))
    for index, stmt in state.pending:
        if stmt.kind == "truncate":
            table = _target_of(_parse(stmt, state.dialect))
            # Not scoped to one source file: this decides whether to DROP a
            # TRUNCATE, and an INSERT against the same target means it is not
            # solo whichever file that INSERT was written in. Claiming "no
            # surviving INSERT pair" over one is false in the same report the
            # INSERT's own Decision appears in.
            paired = table is not None and any(
                compare_targets(insert_table, table) in ("same", "ambiguous")
                for _, insert_table in inserts
            )
            if paired:
                # An INSERT against this target is still pending, so a pass
                # that could have paired them declined to (a column list it
                # could not map, or a qualification it could not confirm).
                # Both halves stay pending together: dropping this one with
                # "no surviving INSERT pair" would contradict the deferral
                # Decision printed beside it in the same report.
                pending.append((index, stmt))
                continue
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "ddl",
                    action=f"dropped solo TRUNCATE: {stmt.raw.text.splitlines()[-1][:60]}",
                    reason=(
                        "a TRUNCATE with no surviving INSERT pair has no dbt equivalent; dbt's "
                        "table materialization rebuilds from scratch on every run"
                    ),
                )
            )
            continue
        if stmt.kind != "ddl_other":
            pending.append((index, stmt))
            continue
        decisions.append(
            _decision(
                stmt,
                index,
                "ddl",
                action=f"dropped DDL statement: {stmt.raw.text.splitlines()[-1][:60]}",
                reason=(
                    "dbt rebuilds objects from scratch; if an index is genuinely needed it "
                    "belongs in a post-hook"
                ),
            )
        )
    return PassState(
        pending=tuple(pending),
        drafts=state.drafts,
        decisions=tuple(decisions),
        dialect=state.dialect,
    )


# A model name dbt can use. dbt takes a model's name from its file and then
# uses that name as a relation identifier in the warehouse and as the argument
# to ref(), so the set of names that work is the set of bare SQL identifiers.
# Anything else would have to be mangled into one, and mangling invents a name
# the reader never wrote -- which is the one thing the naming rule this pass
# exists to teach cannot survive.
_USABLE_MODEL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _distinguish(one: str, other: str) -> tuple[str, str]:
    """The shortest tail of each of two paths that tells them apart.

    Two files called `orders.sql` in two folders are the one way two
    file-named models collide, and a refusal naming them both by filename
    reads as one file twice ("orders.sql asks to be called orders -- and so
    does orders.sql"). Showing the whole absolute path of each would be
    unambiguous and unreadable; showing the part that differs is both.
    """
    first = PurePath(one).parts
    second = PurePath(other).parts
    depth = 1
    while depth < max(len(first), len(second)) and first[-depth:] == second[-depth:]:
        depth += 1
    return str(PurePath(*first[-depth:])), str(PurePath(*second[-depth:]))


def _reads(node: exp.Expr) -> tuple[exp.Table, ...]:
    """Every table this query reads from, its own CTEs excluded.

    `naming.is_external_read` decides, rather than a CTE list assembled here:
    the set of CTEs in scope for a read is a lexical question, and three
    modules were each answering it with the same unscoped `find_all(exp.CTE)`
    -- see that function.
    """
    return tuple(t for t in node.find_all(exp.Table) if is_external_read(t))


def select_pass(state: PassState) -> PassState:
    """A query on its own becomes a model named after the file it is in.

    Every other drafting pass here converts a statement that *writes* a table
    and takes the model's name from that target. A bare `SELECT` writes
    nothing, so it has no target -- and before this pass it was an unhandled
    kind: classified by the ingester ("parsed as a query") and then consumed
    by nothing, which left a script made only of queries producing no model,
    no question and no Decision at all. Measured against 129 real scripts,
    that was 122 of every statement in them.

    It does have a name, and it is not invented: the file it is in. That is
    dbt's own rule -- a model's name *is* its file's name -- so performing the
    conversion teaches the single most important fact about dbt, in the course
    of a conversion the reader wanted anyway.

    The name is only available when it is genuinely free, and the four ways it
    is not are each refused with a Decision naming what stands in the way
    rather than worked around:

    - the file holds more than one query, so one name would have to serve
      several models;
    - the file's name is not a name dbt can use;
    - another statement in this conversion already builds a model of that
      name;
    - the query reads a table of that name, so the model would read itself.

    Each refusal leaves the statement pending and says what to do, because
    every one of them is fixed by renaming or splitting a file -- work the
    reader can do and this engine must not do for them.
    """
    decisions = list(state.decisions)
    drafts = state.drafts
    pending: list[tuple[int, ClassifiedStatement]] = []
    # The file each name this pass has claimed was claimed BY. Only this pass's
    # own drafts appear here, which is exactly the distinction the refusal
    # below has to draw: a name held by a file-named model is held by another
    # *file*, and the only thing that can help is renaming one of the two
    # files; a name held by any other draft is held by a table some statement
    # writes, and moving the query to a file of its own is what helps. One
    # message for both said something false in each -- it named a table that
    # was really a filename, and it told a reader whose query was already
    # alone in its file to put it in a file of its own.
    named_after: dict[str, str] = {}
    # How many queries each file contributes. Counted over `pending` rather
    # than over the original input because that is the question being asked:
    # a file whose other statements were converted by an earlier pass has the
    # queries left here, and those are the ones competing for its name.
    per_file: dict[str, int] = {}
    for _, stmt in state.pending:
        if stmt.kind == "select":
            per_file[stmt.raw.source_file] = per_file.get(stmt.raw.source_file, 0) + 1

    for index, stmt in state.pending:
        if stmt.kind != "select":
            pending.append((index, stmt))
            continue
        source = PurePath(stmt.raw.source_file)
        filename = source.name
        name = source.stem
        count = per_file[stmt.raw.source_file]
        if count > 1:
            pending.append((index, stmt))
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "select.several",
                    action=(
                        f"left as-is: {filename} holds {count} queries, and a file names one model"
                    ),
                    reason=(
                        f"a model takes its name from its file, so {filename} has one name to "
                        f"give and {count} queries wanting it. Put each query in a file of its "
                        "own, named for what it builds, and each becomes a model."
                    ),
                    plain_reason=(
                        f"The file's name is the model's name, and there are {count} queries in "
                        f"{filename} -- one name for {count} things. Split them into a file each "
                        "and they all come through."
                    ),
                )
            )
            continue
        if not _USABLE_MODEL_NAME.match(name):
            pending.append((index, stmt))
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "select.unusable_name",
                    action=f"left as-is: dbt cannot name a model after {filename}",
                    reason=(
                        f"a model's name is its file's name, and dbt uses it as a relation "
                        f"identifier and as the argument to ref() -- letters, digits and "
                        f"underscores, not starting with a digit. {filename} is not one, and "
                        "renaming it here would give the model a name you never wrote."
                    ),
                    plain_reason=(
                        f"The model would be called {name!r}, which is not a name a database "
                        "will accept. Rename the file using letters, numbers and underscores "
                        "only, and the query comes through under that name."
                    ),
                )
            )
            continue
        # Casefolded on BOTH sides, unlike every other identity comparison in
        # this engine. `ModelDraft.identity` leaves a QUOTED name's case alone
        # because two quoted SQL identifiers differing only in case are two
        # different tables, and `replace_draft` is right to keep them apart.
        # What is at stake here is not a table name, though -- it is a *file*
        # name. dbt writes one file per model and takes the model's name from
        # it, so a draft called `Orders` and a file called `orders.sql` are two
        # model files whose names differ only in case, and on macOS and Windows
        # that is one file: whichever is written second is the only one that
        # survives, with nothing recording the loss. Found by review, which
        # reached it with `CREATE TABLE "Orders" AS ...` beside an `orders.sql`
        # and got two drafts and no refusal.
        taken = next((d for d in drafts if d.identity[2].casefold() == name.casefold()), None)
        if taken is not None:
            pending.append((index, stmt))
            claimed_by = named_after.get(name.casefold())
            if claimed_by is not None:
                here, there = _distinguish(stmt.raw.source_file, claimed_by)
                reason = (
                    f"a model's name is its file's name, so {here} asks to be called "
                    f"{name} -- and so does {there}. Two files of one name are two models "
                    "wanting one name. Rename either file and both queries come through, "
                    "each under its own file's name."
                )
                plain = (
                    f"Two files here are both called {filename} -- {here} and {there} -- and "
                    f"the file's name is the model's name, so both ask to be called {name}. "
                    "Rename one of them and they both come through."
                )
            else:
                reason = (
                    f"a model's name is its file's name, so {filename} asks to be called "
                    f"{name} -- and this conversion already builds a model of that name from "
                    f"{taken.qualified_name}, a table one of its own statements writes. Two "
                    "models cannot share a name, and this query is not that table's "
                    "definition, so folding them together would invent a query you never "
                    "wrote. Put the query in a file of its own name and it becomes a model "
                    "of it."
                )
                plain = (
                    f"{name} is already the name of something this conversion builds, and it "
                    f"is the name {filename} asks for. The two are not the same query, so "
                    "neither can be dropped: put this query in a differently named file and "
                    "it comes through under that name."
                )
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "select.name_taken",
                    action=f"left as-is: the model name {name} is already taken in this conversion",
                    reason=reason,
                    plain_reason=plain,
                )
            )
            continue
        node = _parse(stmt, state.dialect)
        identity = (
            "",
            "",
            name.casefold(),
        )
        circular = next(
            (t for t in _reads(node) if compare_keys(target_key(t), identity) == "same"), None
        )
        if circular is not None:
            pending.append((index, stmt))
            decisions.append(
                _decision(
                    stmt,
                    index,
                    "select.reads_itself",
                    action=f"left as-is: a model called {name} would read itself",
                    reason=(
                        f"the query reads {circular.sql(dialect=state.dialect)} and the file is "
                        f"{filename}, so a model named after its file would select from itself: "
                        f"dbt resolves ref('{name}') to the model, never to the table it was "
                        f"named after. dbt's own convention for a model reading one raw table is "
                        f"to prefix it -- stg_{name}.sql -- which leaves the table free to be "
                        "declared as a source."
                    ),
                    plain_reason=(
                        f"The query reads a table called {name} and the file is called "
                        f"{filename}, so the model would be asked to read itself. Rename the "
                        f"file -- stg_{name}.sql is what dbt projects usually call this -- and "
                        "the table it reads stays the table it reads."
                    ),
                )
            )
            continue
        body_node = node.copy()
        body_node.comments = None
        draft = ModelDraft(
            name=name,
            qualified_name=name,
            identity=identity,
            body=body_node.sql(dialect=state.dialect, pretty=True),
            # A query stored nothing and recomputed every time it was run, and
            # a view is the materialization that keeps both true. A table
            # would change what the script does on the way into dbt, which is
            # not a conversion -- and the reader is asked about
            # materialization by name later, with the cost of each spelled out.
            materialization="view",
            grants=(),
            source_indices=(index,),
            leading_comments=tuple(c.strip() for c in (node.comments or ())),
        )
        # Not `replace_draft`: every name it could collide with was refused
        # above, so a collision here would mean this pass had built two drafts
        # for one name behind its own check -- which the check makes
        # impossible, and which "later definition wins" would silently absorb.
        drafts = (*drafts, draft)
        named_after[name.casefold()] = stmt.raw.source_file
        decisions.append(
            _decision(
                stmt,
                index,
                "select",
                action=f"created model {name} (materialized='view')",
                reason=(
                    f"a dbt model is a SELECT in a file and takes its name from that file; this "
                    f"query is in {filename}, so the model is {name}. The query itself is "
                    "unchanged. The script stored nothing, so materialized='view' keeps that "
                    "true -- the query runs whenever something reads the model."
                ),
                plain_reason=(
                    f"Your file is called {filename}, so this is now called {name} -- that is "
                    "the whole naming rule. The query is the one you wrote, untouched. Like "
                    "running it by hand it saves no rows: whatever reads it runs the query."
                ),
            )
        )
    return PassState(
        pending=tuple(pending), drafts=drafts, decisions=tuple(decisions), dialect=state.dialect
    )
