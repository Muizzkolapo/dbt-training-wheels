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
the CTE (FINDING 9). `is_cte_read` casefolds only when *neither* side was
written quoted; if either was, the comparison is exact. One definition,
so the two call sites can never drift apart on this rule either.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

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


def is_cte_read(table: exp.Table, ctes: Iterable[exp.CTE]) -> bool:
    """True when `table`'s bare name reads one of `ctes` by its own alias.

    Comparison is casefolded unless either the CTE's own alias or this
    table's identifier was written quoted — see the module docstring.
    """
    identifier = table.this
    table_quoted = bool(isinstance(identifier, exp.Identifier) and identifier.quoted)
    table_name = table.name
    for cte in ctes:
        alias, alias_quoted = _cte_alias_and_quoted(cte)
        if table_quoted or alias_quoted:
            if table_name == alias:
                return True
        elif table_name.casefold() == alias.casefold():
            return True
    return False
