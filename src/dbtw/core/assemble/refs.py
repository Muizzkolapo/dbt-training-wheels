"""Extracts the tables a model body reads.

sqlglot reports CTE names as tables, so CTE aliases are subtracted; without
that every common table expression would look like an undeclared source.
Only unqualified names are subtracted, though — a CTE alias is never
schema-qualified, so a qualified reference can never actually be a CTE, even
when its bare name happens to collide with one (e.g. `raw.orders` alongside
a CTE named `orders`).

The comparison is casefolded: most SQL dialects (T-SQL among them) treat
unquoted identifiers case-insensitively, so `WITH Totals AS (...) SELECT *
FROM totals` reads the CTE, not some external `totals` table. Comparing the
alias's original case against the read's original case missed that match —
the CTE read looked exactly like an undeclared external reference and could
be rewritten straight past the CTE onto an unrelated model that happened to
share its name, case-insensitively (see rewrite.py, which mirrors this rule
exactly — they must never drift apart).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbtw.core.assemble.types import TableRef


def references_in(body: str, dialect: str | None) -> tuple[TableRef, ...]:
    try:
        node = sqlglot.parse_one(body, read=dialect)
    except SqlglotError:
        return ()
    cte_names = {cte.alias.casefold() for cte in node.find_all(exp.CTE)}
    refs = {
        TableRef(catalog=table.catalog, db=table.db, name=table.name)
        for table in node.find_all(exp.Table)
        if table.name and (table.db or table.catalog or table.name.casefold() not in cte_names)
    }
    return tuple(sorted(refs, key=lambda r: (r.catalog, r.db, r.name)))
