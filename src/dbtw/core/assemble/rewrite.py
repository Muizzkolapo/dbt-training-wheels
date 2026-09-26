"""Rewrites a model body into dbt Jinja.

Table references that resolved to a ref/source (see `resolve.py`) become
`{{ ref('name') }}` / `{{ source('source_name', 'table') }}`; parameters
become `{{ var('name') }}`, or the raw default SQL inlined, for the
variables whose entry in `variables` carries one. Both rewrites happen in a
single sqlglot transform pass.

That map is the only thing deciding a variable's form: a caller keeping one
as a var passes None for it, and there is no second switch that could
disagree with the map about which variables were inlined.

A script variable has two reference forms, both rewritten the same way: a
T-SQL-style `exp.Parameter` (`@name`), and duckdb's `SET VARIABLE`/spark's
`SET VAR` reference form, `GETVARIABLE('name')` — which `variables.py`
already extracts a default for, but which is not an `exp.Parameter` at all.
sqlglot has no first-class node for it in either dialect; it parses as a
plain `exp.Anonymous` function call named `GETVARIABLE` with one string
literal argument, identically for duckdb and spark, which is what makes it
safe to match here: `name.upper() == "GETVARIABLE"` plus exactly one
string-literal argument is unambiguous — a real GETVARIABLE call is the only
thing that shape can be. Before this was matched, `variables.py` consumed
the declaring `SET VARIABLE` statement and the report claimed the reference
was rewritten to `var()`, but the body still called `GETVARIABLE('name')`,
which returns NULL at run time and filters every row (Task 4's fix made
extraction accept the statement without this half of the fix landing too).

An inlined default that is a compound expression (`DECLARE @n INT = 1 + 2`)
must be parenthesized when it's spliced in: sqlglot's `.transform()` swaps
the parsed default node in as a child of whatever expression the reference
sat inside (e.g. `@n * 3`), with no grouping added automatically, so an
unparenthesized `1 + 2` swapped into `@n * 3` prints as `1 + 2 * 3` —
operator precedence silently changes what the expression evaluates to (7,
not the 9 the original script computed with @n substituted as a value).
`naming.maybe_paren` wraps every inlined default except the atomic ones (a
bare literal, NULL, a boolean, or an already-parenthesized default), where
the extra parens would just be noise — `emit.report`'s vars-block renderer
needs the identical rule for the *other* path (the kept `var()` default,
which reaches dbt_project.yml as YAML text instead), so the rule lives in
`naming.py`, not here, where both can import the one definition.

The table rewrite must re-apply the original alias: a bare `exp.Var` or
`exp.Identifier` swapped in for a `FROM raw.orders AS o` silently drops the
`AS o`, dangling every `o.col` reference downstream. Wrapping the injected
`exp.Table(this=exp.Var(...))` with `exp.alias_(..., table=True)` — only when
the original table carried an alias — is the one injection shape that
survives sqlglot's generator intact.

Which tables are rewritten is not decided here: `naming.is_external_read`
decides it, and `refs.py` asks the same function the same question. Only a
read of something outside the query is a candidate — a read of one of the
query's own CTEs is not — and a qualified reference always is, since a CTE
alias is never schema-qualified. The two modules must stay mirror images of
each other: if refs.py excluded a CTE read from `_source_entries`/dependency
edges but this module's own check disagreed, the read would be rewritten
straight past the CTE onto an unrelated model that happened to share its
name, silently substituting different data (the conservative failure mode —
leaving a real table as written with an unresolved Decision — is always the
safe one).

Sharing a *comparison* was not enough, and both modules once got this wrong
in the same way: each built the CTE set it compared against with an unscoped
`find_all(exp.CTE)`, so a CTE named `orders` inside a subquery excluded a
read of `orders` at the top level, where it is a real table — here that
meant the model shipped with a bare warehouse table name where dbt needed a
`ref()`. `is_external_read` takes the table alone and works the scope out
itself, so neither module has a set of its own to get wrong. See its
docstring, and the module docstring of `naming.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeGuard

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbtw.core.assemble.resolve import Resolution
from dbtw.core.naming import is_external_read, maybe_paren


def rewrite_body(
    body: str,
    dialect: str | None,
    resolutions: Mapping[tuple[str, str, str], Resolution],
    variables: Mapping[str, str | None],
) -> str:
    try:
        node = sqlglot.parse_one(body, read=dialect)
    except SqlglotError:
        return body

    def _rewrite_table(table: exp.Table) -> exp.Expr:
        if not is_external_read(table):
            return table
        resolution = resolutions.get((table.catalog, table.db, table.name))
        if resolution is None or resolution.kind == "unresolved":
            return table
        if resolution.kind == "cross_ref":
            # dbt's two-argument ref: the project that builds the model, then
            # the model. It is how one project reads another's model instead
            # of declaring that team's raw table as a source of its own.
            jinja = f"{{{{ ref('{resolution.project}', '{resolution.target}') }}}}"
        elif resolution.kind == "ref":
            jinja = f"{{{{ ref('{resolution.target}') }}}}"
        else:
            jinja = f"{{{{ source('{resolution.source_name}', '{resolution.target}') }}}}"
        new_table = exp.Table(this=exp.Var(this=jinja))
        if table.alias:
            return exp.alias_(new_table, table.alias, table=True)
        return new_table

    def _variable_reference(name: str, fallback: exp.Expr) -> exp.Expr:
        if name not in variables:
            return fallback
        default_sql = variables[name]
        if default_sql is not None:
            try:
                return maybe_paren(sqlglot.parse_one(default_sql, read=dialect))
            except SqlglotError:
                # An un-inlinable default is better rendered as a var call than
                # crashing the whole conversion.
                pass
        return exp.Var(this=f"{{{{ var('{name}') }}}}")

    def _rewrite_parameter(parameter: exp.Parameter) -> exp.Expr:
        return _variable_reference(parameter.name, parameter)

    def _is_getvariable_call(node: exp.Expr) -> TypeGuard[exp.Anonymous]:
        return (
            isinstance(node, exp.Anonymous)
            and node.name.upper() == "GETVARIABLE"
            and len(node.expressions) == 1
            and isinstance(node.expressions[0], exp.Literal)
            and node.expressions[0].is_string
        )

    def _rewrite_getvariable(call: exp.Anonymous) -> exp.Expr:
        name = call.expressions[0].this
        return _variable_reference(name, call)

    def _transform(n: exp.Expr) -> exp.Expr:
        if isinstance(n, exp.Table):
            return _rewrite_table(n)
        if isinstance(n, exp.Parameter):
            return _rewrite_parameter(n)
        if _is_getvariable_call(n):
            return _rewrite_getvariable(n)
        return n

    node = node.transform(_transform)
    return node.sql(dialect=dialect, pretty=True)
