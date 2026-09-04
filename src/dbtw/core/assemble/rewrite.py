"""Rewrites a model body into dbt Jinja.

Table references that resolved to a ref/source (see `resolve.py`) become
`{{ ref('name') }}` / `{{ source('source_name', 'table') }}`; parameters
become `{{ var('name') }}` (or the raw default SQL, inlined, when asked).
Both rewrites happen in a single sqlglot transform pass.

The table rewrite must re-apply the original alias: a bare `exp.Var` or
`exp.Identifier` swapped in for a `FROM raw.orders AS o` silently drops the
`AS o`, dangling every `o.col` reference downstream. Wrapping the injected
`exp.Table(this=exp.Var(...))` with `exp.alias_(..., table=True)` — only when
the original table carried an alias — is the one injection shape that
survives sqlglot's generator intact.

A table's exclusion from rewriting mirrors `refs.py`'s CTE-alias rule
exactly: only an *unqualified* name matching a CTE alias is excluded, since a
CTE alias is never schema-qualified and a qualified reference can never
actually be a CTE.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbtw.core.assemble.resolve import Resolution


def rewrite_body(
    body: str,
    dialect: str | None,
    resolutions: Mapping[tuple[str, str, str], Resolution],
    variables: Mapping[str, str | None],
    inline_vars: bool,
) -> str:
    try:
        node = sqlglot.parse_one(body, read=dialect)
    except SqlglotError:
        return body

    cte_names = {cte.alias for cte in node.find_all(exp.CTE)}

    def _rewrite_table(table: exp.Table) -> exp.Expr:
        is_candidate = bool(table.name) and (
            table.db or table.catalog or table.name not in cte_names
        )
        if not is_candidate:
            return table
        resolution = resolutions.get((table.catalog, table.db, table.name))
        if resolution is None or resolution.kind == "unresolved":
            return table
        if resolution.kind == "ref":
            jinja = f"{{{{ ref('{resolution.target}') }}}}"
        else:
            jinja = f"{{{{ source('{resolution.source_name}', '{resolution.target}') }}}}"
        new_table = exp.Table(this=exp.Var(this=jinja))
        if table.alias:
            return exp.alias_(new_table, table.alias, table=True)
        return new_table

    def _rewrite_parameter(parameter: exp.Parameter) -> exp.Expr:
        name = parameter.name
        if name not in variables:
            return parameter
        default_sql = variables[name]
        if inline_vars and default_sql is not None:
            try:
                return sqlglot.parse_one(default_sql, read=dialect)
            except SqlglotError:
                # An un-inlinable default is better rendered as a var call than
                # crashing the whole conversion.
                pass
        return exp.Var(this=f"{{{{ var('{name}') }}}}")

    def _transform(n: exp.Expr) -> exp.Expr:
        if isinstance(n, exp.Table):
            return _rewrite_table(n)
        if isinstance(n, exp.Parameter):
            return _rewrite_parameter(n)
        return n

    node = node.transform(_transform)
    return node.sql(dialect=dialect, pretty=True)
