"""Shared sqlglot-adjacent rules that must never drift between modules.

Two independent pieces of shared logic live here, for the same reason: each
was once defined once, needed identically by two+ modules that must never
disagree about it, and had — or would have — quietly drifted otherwise. This
module sits directly under `dbtw.core`, a sibling of `passes`, `assemble`,
and `emit`, depending on none of them, precisely so any of them can import
from here without risking a cycle.

## Table-reference qualification

Before slice-6a's final review, `_qualified` (the dotted `catalog.db.name`
join) was triplicated byte-for-byte across `passes.tier1`, `assemble.assembler`,
and `assemble.resolve` (there as `_qualified_key`) — and the qualification
predicate that decides whether a reference is schema/catalog-qualified at all
had quietly drifted between copies: `passes.tier1`/`assemble.assembler` tested
`if ref.db:` while `assemble.resolve` correctly tested `db or catalog`. A
catalog-only reference (e.g. Snowflake's `mydb..orders`) then fell through
`assembler`'s qualified-dependency and source-entry logic as if it were a
plain unqualified bare name, while `resolve` correctly refused to bare-name
match it — so the emitted report could claim a dependency edge (or a source)
that the Decisions and the rewritten body both say never resolved.

`qualified_name`/`is_qualified` work on anything with `.catalog`, `.db`,
`.name` string attributes/properties — both `assemble.types.TableRef` and
sqlglot's `exp.Table` satisfy this structurally, without either being
imported here.

## SQL-literal atomicity

`assemble.rewrite`'s `--inline-vars` path parenthesizes a compound default
(`DECLARE @n INT = 1 + 2`) before splicing it into the AST, since sqlglot's
`.transform()` adds no grouping on its own: an unparenthesized `1 + 2`
spliced into `@n * 3` prints as `1 + 2 * 3`, silently changing what the
expression evaluates to under normal operator precedence. `emit.report`'s
vars-block renderer writes the same `default_sql` text into YAML for the
*other* path (`var('n')`, the default, more common one) — and needs the
exact same parenthesization, or the two paths compute different effective
SQL for the same variable (FINDING 7). `is_atomic_sql`/`maybe_paren` are the
one shared answer to "does this default need defensive parens", so both
paths can never disagree about it again.

## CTE-alias matching

`assemble.refs` and `assemble.rewrite` each independently decide whether a
table read is actually a read of one of the body's own CTEs (and so must
never be treated as an external reference or a rewrite candidate). Both
originally compared original case only, missing that most dialects treat
unquoted identifiers case-insensitively (`WITH Totals AS (...) SELECT *
FROM totals` reads the CTE). Casefolding both sides fixed that — but
unconditionally, which broke the opposite, genuinely case-sensitive case: a
QUOTED identifier's case *is* significant in every dialect sqlglot supports,
so `WITH "Totals" AS (...) SELECT * FROM "totals"` (postgres) are two
different names, and casefolding silently swallowed the read as if it were
the CTE (FINDING 9). `same_identifier` casefolds only when *neither* side
was written quoted; if either was, the comparison is exact.

Sharing the comparison was not enough, because what it was *applied to* was
rebuilt at each call site -- and all three rebuilt it the same wrong way,
with `tuple(node.find_all(exp.CTE))`. That collects every CTE in the whole
statement, so a CTE named `orders` inside a subquery subtracted a read of
`orders` anywhere else in the query, including at the top level where it is
a real table. The consequence was invisible in each caller and different in
each: `refs` dropped the table from a body's references, so it was never
declared as a source and never recorded as a dependency; `rewrite` skipped
it, so the model shipped with a bare warehouse table name where dbt needed
a `ref()`; and `passes.tier1.select_pass`, whose whole refusal is that a
model must not read a table of its own name, found nothing to refuse and
built one. `is_external_read` therefore takes the *table* and nothing else,
and works the scope out itself: a CTE's name reaches only the query its
`WITH` is attached to, and within one `WITH` a CTE's body sees the CTEs
written before it -- plus itself, when the `WITH` is `RECURSIVE`. One
definition, so no caller has a set of its own to get wrong.

## Cross-statement target identity

`passes.tier2`'s `append_pass` decides whether a pending DELETE and a
pending INSERT name the *same* table, to defer converting the INSERT into
an append incremental when they do — that pairing is catalog 2.3, a
delete-then-insert rebuild of one slice, and appending instead would
silently keep rows outside that slice that the DELETE removed. A naive
`qualified_name` string-equality check gets this wrong two ways: it misses
differently-cased spellings (`DELETE FROM Events` / `INSERT INTO events`
are the same table on every dialect that folds unquoted identifiers), and,
worse, it calls two statements "different" whenever they merely *qualify*
their target to different degrees (`DELETE FROM db.events` / `INSERT INTO
events`) — but whether an unqualified name resolves to the same object as a
qualified one depends on the session's default schema/catalog, which the
SQL text never reveals. Confidently converting on a wrong "different" is
the failure mode that matters here: it ships an incremental whose semantics
silently diverge from the script. `same_identifier` generalizes the
casefold-unless-quoted rule CTE-alias matching established (every caller
now shares it, so it is defined once); `compare_targets` builds on it to return `"same"`,
`"different"`, or `"ambiguous"` for two parsed targets — the third outcome
exists precisely so a caller can refuse to guess instead of silently
picking a side.
"""

from __future__ import annotations

from typing import Literal, Protocol

from sqlglot import exp


class Qualifiable(Protocol):
    """Structural shape shared by `TableRef` and sqlglot's `exp.Table`.

    Declared as read-only properties, not plain attributes: `exp.Table`
    exposes `catalog`/`db`/`name` as computed `@property` getters and
    `TableRef` is a frozen dataclass, so neither is writable — a plain
    `catalog: str` annotation here would demand write access neither type
    offers and reject both.
    """

    @property
    def catalog(self) -> str: ...
    @property
    def db(self) -> str: ...
    @property
    def name(self) -> str: ...


def qualified_name(ref: Qualifiable) -> str:
    """Dotted catalog.db.name, dropping empty parts; bare name if unqualified."""
    return ".".join(part for part in (ref.catalog, ref.db, ref.name) if part)


def is_qualified(ref: Qualifiable) -> bool:
    """True when the reference carries a schema and/or catalog the author wrote.

    Two fields, not one, carry qualification. `ref.catalog` alone (Snowflake's
    `mydb..orders`, parsed as catalog="mydb", db="") is still qualified — it
    must never bare-name-match a draft or an existing model. Testing `ref.db`
    alone reopens exactly the identity-bug class this module exists to close.
    """
    return bool(ref.db) or bool(ref.catalog)


# Node types that never need defensive parens: a bare literal, NULL, or
# boolean can't have its meaning changed by the surrounding expression's
# operator precedence, and an already-parenthesized expression is already a
# self-contained unit — wrapping it again would just double the parens.
_ATOMIC_SQL_TYPES = (exp.Literal, exp.Boolean, exp.Null, exp.Paren)


def is_atomic_sql(node: exp.Expr) -> bool:
    """True when `node` can't have its meaning changed by whatever operator
    precedence it ends up embedded in — see the module docstring.
    """
    return isinstance(node, _ATOMIC_SQL_TYPES)


def maybe_paren(node: exp.Expr) -> exp.Expr:
    """Wrap `node` in `exp.Paren` unless `is_atomic_sql(node)`."""
    if is_atomic_sql(node):
        return node
    return exp.Paren(this=node)


def _cte_alias_and_quoted(cte: exp.CTE) -> tuple[str, bool]:
    """A CTE's own alias text and whether it was written quoted."""
    alias_node = cte.args.get("alias")
    identifier = alias_node.this if isinstance(alias_node, exp.TableAlias) else None
    quoted = bool(isinstance(identifier, exp.Identifier) and identifier.quoted)
    return cte.alias, quoted


def same_identifier(a: str, a_quoted: bool, b: str, b_quoted: bool) -> bool:
    """True when two identifier spellings denote the same name.

    Casefolded unless either was written quoted — quoted identifiers are
    case-sensitive in every dialect that respects quoting (Postgres,
    Snowflake, ...), so a quoted `"Events"` and a bare `events` are two
    different names even though bare `Events` and `events` are the same
    one. CTE-alias matching established this rule first (now
    `is_external_read`); `compare_targets` reuses it for cross-statement
    target identity; the assembler reuses it to match a `--unique-key` value
    against a model's own output columns, and `merge_pass` to match a
    MERGE's SET/INSERT column lists — see the module docstring.
    """
    if a_quoted or b_quoted:
        return a == b
    return a.casefold() == b.casefold()


def _own_with(node: exp.Expr) -> exp.With | None:
    """The `WITH` clause this query node carries, if it carries one.

    Read off `args` by type rather than by key, because the key sqlglot
    stores it under is not stable across its versions (`"with"` became
    `"with_"`), and a lookup that silently misses would make every CTE
    invisible -- turning every CTE read into an external table reference,
    which is the failure this whole section exists to prevent. A query node
    carries at most one.
    """
    for value in node.args.values():
        if isinstance(value, exp.With):
            return value
    return None


def _enclosing_cte(node: exp.Expr, with_clause: exp.With) -> exp.CTE | None:
    """The CTE of `with_clause` that `node` sits inside, if it sits in one."""
    current = node.parent
    while current is not None:
        if isinstance(current, exp.CTE) and current.parent is with_clause:
            return current
        current = current.parent
    return None


def _visible_cte_aliases(table: exp.Table) -> list[tuple[str, bool]]:
    """Every CTE alias in scope where `table` is written, innermost outward.

    Lexical scope, not "every CTE in the statement". A `WITH` attached to a
    subquery names its CTEs only inside that subquery, so

        SELECT id FROM orders
        UNION ALL
        SELECT id FROM (WITH orders AS (...) SELECT id FROM orders) AS sub

    has one CTE named `orders` and two reads of the name, and only the inner
    one is that CTE -- the outer `FROM orders` reads a real table. Collecting
    CTEs with an unscoped `find_all(exp.CTE)` (which is what all three callers
    of the old `is_cte_read` did) made the outer read look like a CTE read
    too, so it was subtracted from a body's references: it was never rewritten
    to `ref()`/`source()` and never recorded as a dependency, and in
    `select_pass` a model reading a table of its own name shipped as a success.

    Within one `WITH`, a CTE's own body sees only the CTEs written *before*
    it -- and itself, when the `WITH` is `RECURSIVE`. So in
    `WITH orders AS (SELECT * FROM orders) SELECT * FROM orders` the inner
    read is the real table (nothing precedes `orders`, and the `WITH` is not
    recursive) while the outer read is the CTE; in
    `WITH RECURSIVE t AS (... FROM t) SELECT * FROM t` both reads are the CTE.
    """
    visible: list[tuple[str, bool]] = []
    ancestor = table.parent
    while ancestor is not None:
        with_clause = _own_with(ancestor)
        if with_clause is not None:
            ctes = list(with_clause.expressions)
            own = _enclosing_cte(table, with_clause)
            if own is None:
                in_scope = ctes
            else:
                position = ctes.index(own)
                in_scope = (
                    ctes[: position + 1] if with_clause.args.get("recursive") else ctes[:position]
                )
            visible.extend(_cte_alias_and_quoted(cte) for cte in in_scope)
        ancestor = ancestor.parent
    return visible


def is_external_read(table: exp.Table) -> bool:
    """Whether `table` reads something from outside the query it is written in.

    The one answer to "is this table reference a real table, or one of the
    query's own CTEs" -- and the only thing three modules should ask, because
    each of them needs the same answer and a wrong one is invisible: a CTE
    mistaken for a table is announced as an undeclared source, and a table
    mistaken for a CTE is silently dropped from the body's references.

    A qualified reference is always external: a CTE alias is never
    schema-qualified, so `raw.orders` cannot be a CTE even where a CTE called
    `orders` exists. An unqualified one is external unless a CTE in scope
    (`_visible_cte_aliases`) shares its name, compared by `same_identifier`
    -- casefolded unless either side was written quoted, since a quoted
    `"Totals"` and a bare `totals` are two different names.

    A reference with no name at all (a table function, a subquery in a FROM)
    is not a read of anything nameable and is neither.
    """
    if not table.name:
        return False
    if table.db or table.catalog:
        return True
    identifier = table.this
    quoted = bool(isinstance(identifier, exp.Identifier) and identifier.quoted)
    return not any(
        same_identifier(table.name, quoted, alias, alias_quoted)
        for alias, alias_quoted in _visible_cte_aliases(table)
    )


TargetComparison = Literal["same", "different", "ambiguous"]

_TargetPart = Literal["name", "db", "catalog"]


def _table_part(table: exp.Table, part: _TargetPart) -> tuple[str, bool]:
    """`table`'s name/db/catalog text and whether it was written quoted."""
    if part == "name":
        text, node = table.name, table.this
    elif part == "db":
        text, node = table.db, table.args.get("db")
    else:
        text, node = table.catalog, table.args.get("catalog")
    quoted = bool(isinstance(node, exp.Identifier) and node.quoted)
    return text, quoted


def compare_keys(a: tuple[str, str, str], b: tuple[str, str, str]) -> TargetComparison:
    """`compare_targets`, over two `target_key` triples rather than two parsed
    targets, for callers holding a stored identity instead of an `exp.Table`.

    The same three outcomes and the same reasons: `"different"` when the
    names disagree or a part both sides wrote disagrees, `"ambiguous"` when
    one side leaves a db or catalog unwritten where the other supplies one
    (`events` vs `db.events` — which one an unqualified name resolves to
    depends on the session's default schema, not on the SQL), `"same"` when
    every part agrees or is unwritten on both sides. Quoting is already
    folded into the triples, so the comparison is plain equality here.
    """
    catalog_a, db_a, name_a = a
    catalog_b, db_b, name_b = b
    if name_a != name_b:
        return "different"
    ambiguous = False
    for part_a, part_b in ((db_a, db_b), (catalog_a, catalog_b)):
        if part_a and part_b:
            if part_a != part_b:
                return "different"
        elif part_a or part_b:
            ambiguous = True
    return "ambiguous" if ambiguous else "same"


def target_key(table: exp.Table) -> tuple[str, str, str]:
    """A hashable identity for a table target, for keying one target against
    another the way `compare_targets` compares them.

    Each part is casefolded unless it was written quoted, so two keys are
    equal exactly when `compare_targets` answers `"same"`. It answers a
    narrower question than `compare_targets` though: equal keys are a
    confirmed match, but unequal keys are NOT a confirmed non-match, because
    two targets qualified to different degrees (`events` vs `db.events`) are
    `"ambiguous"`, not `"different"`. A caller keying a lookup on this must
    read a miss as "no confirmed pair", never as "a different table".
    """
    catalog, catalog_quoted = _table_part(table, "catalog")
    db, db_quoted = _table_part(table, "db")
    name, name_quoted = _table_part(table, "name")
    return (
        catalog if catalog_quoted else catalog.casefold(),
        db if db_quoted else db.casefold(),
        name if name_quoted else name.casefold(),
    )


def compare_targets(a: exp.Table, b: exp.Table) -> TargetComparison:
    """Compare two parsed table targets for identity — see the module
    docstring's "Cross-statement target identity" section for why a plain
    same/different split isn't safe here.

    - `"different"`: the names disagree (by `same_identifier`), or both
      sides wrote a db and/or catalog and one of those disagrees. Two
      targets that both spell out their schema/catalog and disagree on it
      are unambiguously different, whatever their bare names look like.
    - `"ambiguous"`: the names agree, and no part both sides wrote
      disagrees, but one side leaves a db and/or catalog unwritten where
      the other supplies one (`events` vs. `db.events`, `events` vs.
      `mydb..events`). Whether they're the same table then depends on the
      session's default schema/catalog, which the SQL text never reveals
      — callers must not treat this as either a confirmed match or a
      confirmed non-match.
    - `"same"`: every part agrees, or is unwritten on both sides.
    """
    name_a, name_a_quoted = _table_part(a, "name")
    name_b, name_b_quoted = _table_part(b, "name")
    if not same_identifier(name_a, name_a_quoted, name_b, name_b_quoted):
        return "different"
    ambiguous = False
    for part in ("db", "catalog"):
        text_a, quoted_a = _table_part(a, part)
        text_b, quoted_b = _table_part(b, part)
        if text_a and text_b:
            if not same_identifier(text_a, quoted_a, text_b, quoted_b):
                return "different"
        elif text_a or text_b:
            ambiguous = True
    return "ambiguous" if ambiguous else "same"
