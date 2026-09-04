"""Extracts the tables a model body reads.

sqlglot reports CTE names as tables, so CTE aliases are subtracted; without
that every common table expression would look like an undeclared source.
Only unqualified names are subtracted, though — a CTE alias is never
schema-qualified, so a qualified reference can never actually be a CTE, even
when its bare name happens to collide with one (e.g. `raw.orders` alongside
a CTE named `orders`).

The comparison (`naming.is_cte_read`) is casefolded unless either the CTE's
alias or the read's identifier was written quoted, in which case it's exact:
most SQL dialects (T-SQL among them) treat unquoted identifiers
case-insensitively, so `WITH Totals AS (...) SELECT * FROM totals` reads the
CTE, not some external `totals` table — but a QUOTED identifier's case *is*
significant in every dialect sqlglot supports, so `WITH "Totals" AS (...)
SELECT * FROM "totals"` (postgres) names two different tables, and
casefolding unconditionally would swallow the quoted read as if it were the
CTE. See rewrite.py, which mirrors this rule exactly via the same shared
`naming.is_cte_read` — they must never drift apart.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbtw.core.assemble.types import TableRef
from dbtw.core.naming import is_cte_read


def references_in(body: str, dialect: str | None) -> tuple[TableRef, ...]:
    try:
        node = sqlglot.parse_one(body, read=dialect)
    except SqlglotError:
        return ()
    ctes = tuple(node.find_all(exp.CTE))
    refs = {
        TableRef(catalog=table.catalog, db=table.db, name=table.name)
        for table in node.find_all(exp.Table)
        if table.name and (table.db or table.catalog or not is_cte_read(table, ctes))
    }
    return tuple(sorted(refs, key=lambda r: (r.catalog, r.db, r.name)))
