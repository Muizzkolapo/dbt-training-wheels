"""Extracts the tables a model body reads.

sqlglot reports CTE names as tables, so CTE aliases are subtracted; without
that every common table expression would look like an undeclared source.
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
    cte_names = {cte.alias for cte in node.find_all(exp.CTE)}
    refs = {
        TableRef(catalog=table.catalog, db=table.db, name=table.name)
        for table in node.find_all(exp.Table)
        if table.name and table.name not in cte_names
    }
    return tuple(sorted(refs, key=lambda r: (r.catalog, r.db, r.name)))
