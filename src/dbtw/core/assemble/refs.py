"""Extracts the tables a model body reads.

sqlglot reports CTE names as tables, so a read of one of the query's own CTEs
has to be subtracted; without that every common table expression would look
like an undeclared source. `naming.is_external_read` is the one place that
decides, and `rewrite.py` asks it the same question about the same body —
they must never drift apart, because a read this module subtracts and that
one rewrites would be pointed at an unrelated model sharing its name.

Two properties of that decision matter to every caller of this module. Only
unqualified names are subtracted: a CTE alias is never schema-qualified, so
`raw.orders` is a real table even beside a CTE called `orders`. And the name
comparison is casefolded unless either side was written quoted, in which case
it is exact — most dialects (T-SQL among them) treat unquoted identifiers
case-insensitively, so `WITH Totals AS (...) SELECT * FROM totals` reads the
CTE, while a QUOTED identifier's case *is* significant, so
`WITH "Totals" AS (...) SELECT * FROM "totals"` (postgres) names two
different tables and casefolding unconditionally would swallow the quoted
read as if it were the CTE.

What this module got wrong for longer than either of those: the CTE set it
compared against was built here, with an unscoped `find_all(exp.CTE)`. That
is every CTE in the whole statement, so a CTE named `orders` inside a
subquery subtracted a read of `orders` at the top level — a real table, which
then appeared in no reference list, was declared as no source, and was
recorded as no dependency. `is_external_read` takes the table alone and
derives the scope itself, so there is no set here to get wrong.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbtw.core.assemble.types import TableRef
from dbtw.core.naming import is_external_read


def references_in(body: str, dialect: str | None) -> tuple[TableRef, ...]:
    try:
        node = sqlglot.parse_one(body, read=dialect)
    except SqlglotError:
        return ()
    refs = {
        TableRef(catalog=table.catalog, db=table.db, name=table.name)
        for table in node.find_all(exp.Table)
        if is_external_read(table)
    }
    return tuple(sorted(refs, key=lambda r: (r.catalog, r.db, r.name)))
