"""
SQL parsing utilities using sqlglot for robust BigQuery SQL parsing.

This module provides proper AST-based parsing for:
- DECLARE/SET statement extraction and transformation
- Variable resolution and CTE conversion

The dbt-specific transformations (source()/ref()) remain in sql_parser.py.
"""

import re
from dataclasses import dataclass

try:
    import sqlglot
    from sqlglot import exp

    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False
    sqlglot = None
    exp = None


@dataclass
class DeclaredVariable:
    """Represents a DECLARE'd variable in BigQuery SQL."""

    name: str
    var_type: str
    value: str | None = None
    value_sql: str | None = None  # The original SQL for the value


def extract_declared_variables(sql: str) -> list[DeclaredVariable]:
    """
    Extract all DECLARE'd variables and their SET values from SQL.

    Uses sqlglot for proper AST-based parsing to handle:
    - DECLARE var1, var2 type;
    - DECLARE var type DEFAULT value;
    - SET var = (SELECT ...);
    - SET var = expression;

    Args:
        sql: The SQL content to parse

    Returns:
        List of DeclaredVariable objects
    """
    if not SQLGLOT_AVAILABLE or sqlglot is None or exp is None:
        return []

    try:
        statements = sqlglot.parse(sql, dialect="bigquery")
    except Exception:
        return []

    variables: dict[str, DeclaredVariable] = {}

    for stmt in statements:
        if stmt is None:
            continue

        # Handle DECLARE statements
        if isinstance(stmt, exp.Declare):
            for expr in stmt.expressions:
                # expr is a DeclareItem
                this = expr.this  # Variable name(s)
                kind = expr.args.get("kind")  # The type
                default = expr.args.get("default")  # DEFAULT value if any

                # Handle multiple variables in one DECLARE
                if isinstance(this, list):
                    var_names = [v.name if hasattr(v, "name") else str(v) for v in this]
                else:
                    var_names = [this.name if hasattr(this, "name") else str(this)]

                # Get the type
                var_type = (
                    kind.sql(dialect="bigquery") if kind and hasattr(kind, "sql") else str(kind) if kind else "UNKNOWN"
                )

                # Get default value if present
                default_sql = default.sql(dialect="bigquery") if default and hasattr(default, "sql") else None

                for var_name in var_names:
                    variables[var_name] = DeclaredVariable(
                        name=var_name, var_type=var_type, value=default_sql, value_sql=default_sql
                    )

        # Handle SET statements
        elif isinstance(stmt, exp.Set):
            for set_item in stmt.expressions:
                if hasattr(set_item, "this") and isinstance(set_item.this, exp.EQ):
                    eq = set_item.this
                    # Get variable name from LHS
                    var_col = eq.this
                    if isinstance(var_col, exp.Column):
                        var_name = var_col.this.name if hasattr(var_col.this, "name") else str(var_col.this)
                    else:
                        var_name = var_col.name if hasattr(var_col, "name") else str(var_col)

                    # Get value from RHS
                    value_expr = eq.expression
                    value_sql = (
                        value_expr.sql(dialect="bigquery")
                        if value_expr and hasattr(value_expr, "sql")
                        else str(value_expr)
                    )

                    # Update or create the variable
                    if var_name in variables:
                        variables[var_name].value = value_sql
                        variables[var_name].value_sql = value_sql
                    else:
                        variables[var_name] = DeclaredVariable(
                            name=var_name, var_type="UNKNOWN", value=value_sql, value_sql=value_sql
                        )

    return list(variables.values())


def transform_variables_to_ctes(sql: str, variables: list[DeclaredVariable] | None = None) -> str:
    """
    Transform DECLARE/SET variables into CTEs for dbt compatibility.

    This:
    1. Removes DECLARE and SET statements
    2. Creates CTEs for variables with subquery values
    3. Replaces variable references with CTE subqueries

    Args:
        sql: The SQL content with DECLARE/SET statements
        variables: Optional pre-extracted variables

    Returns:
        Transformed SQL with CTEs instead of DECLARE/SET
    """
    if variables is None:
        variables = extract_declared_variables(sql)

    if not variables:
        return sql

    # Build replacement mapping
    var_replacements: dict[str, str] = {}
    ctes_to_add: list[str] = []

    for var in variables:
        if not var.value_sql:
            continue

        value = var.value_sql.strip()

        # Check if it's a subquery (starts with SELECT or is wrapped in parens with SELECT)
        is_subquery = value.upper().startswith("SELECT") or (value.startswith("(") and "SELECT" in value.upper())

        if is_subquery:
            # Create a CTE for this variable
            cte_name = f"__var_{var.name}"

            # Remove outer parens if present
            subquery = value
            if subquery.startswith("(") and subquery.endswith(")"):
                subquery = subquery[1:-1].strip()

            ctes_to_add.append(f"{cte_name} AS ({subquery})")
            var_replacements[var.name] = f"(SELECT * FROM {cte_name})"
        else:
            # Simple expression - inline it
            var_replacements[var.name] = f"({value})"

    # Now process the SQL
    transformed = sql

    # Remove DECLARE statements using regex (more reliable than regenerating from AST)
    declare_pattern = r"DECLARE\s+[\w\s,]+\s+(?:DATE|STRING|INT64|FLOAT64|BOOL|BOOLEAN|TIMESTAMP|DATETIME|NUMERIC|BIGNUMERIC|BYTES|ARRAY|STRUCT|TEXT)(?:\s+DEFAULT\s+[^;]+)?;"
    transformed = re.sub(declare_pattern, "", transformed, flags=re.IGNORECASE)

    # Remove SET statements
    # This pattern handles: SET var = (subquery); and SET var = expression;
    set_pattern = r"SET\s+\w+\s*=\s*(?:\([^)]*(?:\([^)]*\)[^)]*)*\)|[^;]+);"
    transformed = re.sub(set_pattern, "", transformed, flags=re.IGNORECASE)

    # Remove debug SELECT statements (commonly follow DECLARE/SET)
    # Pattern: SELECT var1, var2, ...; followed by comment or newlines
    debug_select_pattern = r"SELECT\s+[\w\s,()]+;(?=\s*[-/\n])"
    transformed = re.sub(debug_select_pattern, "", transformed, flags=re.IGNORECASE)

    # Apply variable replacements to CTE definitions first
    # This handles cases where one variable references another in its SET
    for i, cte in enumerate(ctes_to_add):
        for var_name, replacement in var_replacements.items():
            if f"__var_{var_name}" not in cte:  # Don't replace self-reference
                pattern = rf"\b{re.escape(var_name)}\b"
                ctes_to_add[i] = re.sub(pattern, replacement, ctes_to_add[i])

    # Apply variable replacements to the main SQL
    for var_name, replacement in var_replacements.items():
        pattern = rf"\b{re.escape(var_name)}\b"
        transformed = re.sub(pattern, replacement, transformed)

    # Add CTEs to the SQL
    if ctes_to_add:
        # Clean up whitespace at the start
        transformed = transformed.strip()
        lines = transformed.split("\n")
        cleaned_lines = []
        found_content = False
        for line in lines:
            if line.strip() or found_content:
                found_content = True
                cleaned_lines.append(line)
        transformed = "\n".join(cleaned_lines)

        # Check if already has WITH clause
        if re.match(r"^\s*WITH\s+", transformed, re.IGNORECASE):
            # Add our CTEs to existing WITH
            cte_prefix = ",\n    ".join(ctes_to_add)
            transformed = re.sub(r"^(\s*WITH\s+)", rf"\1{cte_prefix},\n    ", transformed, count=1, flags=re.IGNORECASE)
        else:
            # Add new WITH clause
            cte_block = "WITH " + ",\n    ".join(ctes_to_add) + "\n\n"
            transformed = cte_block + transformed

    # Clean up multiple blank lines
    transformed = re.sub(r"\n{3,}", "\n\n", transformed)

    return transformed.strip()


def _strip_outer_parens(sql: str) -> str:
    """Strip outer parentheses if they wrap the entire SQL."""
    sql = sql.strip()
    if not (sql.startswith("(") and sql.endswith(")")):
        return sql
    depth = 0
    for i, char in enumerate(sql):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and i < len(sql) - 1:
                return sql
    return sql[1:-1].strip()


def extract_sql_for_table_sqlglot(sql: str, table_name: str) -> str | None:
    """
    Extract the SQL logic for a specific table using sqlglot AST parsing.

    This is more reliable than regex for separating CREATE statements.

    Args:
        sql: The full SQL content
        table_name: Name of the table to extract SQL for

    Returns:
        Extracted SQL content (the SELECT/WITH part) or None if not found
    """
    if not SQLGLOT_AVAILABLE or sqlglot is None or exp is None:
        return None

    try:
        statements = sqlglot.parse(sql, dialect="bigquery")
    except Exception:
        return None

    for stmt in statements:
        if stmt is None:
            continue

        # Handle CREATE TABLE/VIEW statements
        if isinstance(stmt, exp.Create) and stmt.this:
            table = stmt.this
            # Get the table name from the CREATE statement
            stmt_table_name = table.this.name if hasattr(table.this, "name") else str(table.this)

            # Check if this matches our target table
            if stmt_table_name.lower() == table_name.lower():
                # Get the query part (the AS SELECT/WITH...)
                query = stmt.expression
                if query and hasattr(query, "sql"):
                    result = query.sql(dialect="bigquery")
                    # Strip outer parentheses from BigQuery's CREATE TABLE ... AS (...) syntax
                    return _strip_outer_parens(result)
                return None

        # Handle INSERT INTO statements
        if isinstance(stmt, exp.Insert) and stmt.this:
            table = stmt.this
            stmt_table_name = table.this.name if hasattr(table.this, "name") else str(table.this)

            if stmt_table_name.lower() == table_name.lower():
                # Get the query part
                query = stmt.expression
                if query and hasattr(query, "sql"):
                    result = query.sql(dialect="bigquery")
                    return _strip_outer_parens(result)
                return None

    return None


def _protect_comments(sql: str) -> tuple[str, dict[str, str]]:
    """
    Replace SQL comments with placeholders to protect them during AST transformation.

    sqlglot can mangle multi-line comments, so we temporarily replace them with placeholders.

    Returns:
        Tuple of (SQL with placeholders, dict mapping placeholder -> original comment)
    """
    import re

    comment_map = {}
    protected_sql = sql

    # Find all single-line comments (-- comment)
    # IMPORTANT: Don't match comments inside strings
    comment_pattern = r"--[^\n]*"
    comments = re.findall(comment_pattern, protected_sql)

    for i, comment in enumerate(comments):
        placeholder = f"__COMMENT_PROTECTED_{i}__"
        comment_map[placeholder] = comment
        # Replace first occurrence
        protected_sql = protected_sql.replace(comment, placeholder, 1)

    return protected_sql, comment_map


def _restore_comments(sql: str, comment_map: dict[str, str]) -> str:
    """Restore original comments from placeholders."""
    restored_sql = sql
    for placeholder, original_comment in comment_map.items():
        restored_sql = restored_sql.replace(placeholder, original_comment)
    return restored_sql


def _strip_non_standard_sql(sql: str) -> tuple[str, list[str]]:
    """
    Strip non-standard BigQuery statements that shouldn't be in dbt models.

    Removes:
    - EXPORT DATA statements
    - Other BigQuery-specific extensions

    Returns:
        Tuple of (cleaned SQL, list of TODO comments for stripped statements)
    """
    import re

    todo_comments = []
    cleaned = sql

    # Remove EXPORT DATA statements
    # Pattern matches: EXPORT DATA OPTIONS (...) AS (SELECT ... FROM ...);
    # Using non-greedy matching and DOTALL to handle multiline
    export_pattern = r"EXPORT\s+DATA\s+OPTIONS\s*\(.*?\)\s*AS\s*\(.*?\)\s*;"
    exports = re.findall(export_pattern, cleaned, re.IGNORECASE | re.DOTALL)

    if exports:
        for i, export_stmt in enumerate(exports, 1):
            # Extract the target from the EXPORT statement
            uri_match = re.search(r"uri\s*=\s*[^\n,)]+", export_stmt, re.IGNORECASE)
            uri = uri_match.group(0).strip() if uri_match else "unknown location"

            todo_comments.append(
                f"-- TODO: Original EXPORT DATA statement #{i} removed (exports to GCS)\n"
                f"--       {uri}\n"
                f"--       Use a separate post-dbt job/script for data exports"
            )

        cleaned = re.sub(export_pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL)

    return cleaned, todo_comments


def replace_all_table_references_unified(
    sql: str,
    external_tables: list[dict] | None = None,
    internal_replacements: dict[str, str] | None = None,
    cross_project_decisions: dict[str, dict] | None = None,
) -> tuple[str, list[str], bool]:
    """
    Unified AST-based table reference replacement for both external and internal tables.

    This handles:
    - External tables (fully qualified names) → source() or ref() calls
    - Internal tables (simple names) → ref() calls
    - Cross-project references
    - Alias preservation (automatic via AST)
    - Strips non-standard SQL (EXPORT DATA, etc.)

    Args:
        sql: The SQL content to transform
        external_tables: List of external table dicts from analyze_sql_content()
            Each dict should contain:
            - 'table': Full qualified table name (e.g., 'project.dataset.table')
            - 'suggestedSource': The source() call to use
            - 'suggestedRef': The ref() call to use (for self-references)
            - 'isSelfReference': Boolean flag
        internal_replacements: Dict mapping internal table names to ref() calls
            e.g., {"orders": "{{ ref('int__orders') }}"}
        cross_project_decisions: Dict mapping "dataset.table" to decision dict
            Each decision dict should contain:
            - 'use_cross_ref': Boolean - whether to use cross-project ref
            - 'project': Project name
            - 'model': Model name

    Returns:
        Tuple of (transformed SQL, list of replacements made, success flag)
    """
    if not SQLGLOT_AVAILABLE or sqlglot is None or exp is None:
        return sql, [], False

    # Strip non-standard SQL first
    sql, todo_comments = _strip_non_standard_sql(sql)

    if not external_tables and not internal_replacements:
        return sql, [], True

    external_tables = external_tables or []
    internal_replacements = internal_replacements or {}
    cross_project_decisions = cross_project_decisions or {}

    replacements_made: list[str] = []
    placeholder_map: dict[str, str] = {}

    # Build unified lookup table
    # Key: normalized table name → (original_name, replacement, match_type)
    # match_type: "full" | "dataset.table" | "table"
    lookup_table: dict[str, tuple[str, str, str]] = {}

    # Add external tables to lookup
    for table_info in external_tables:
        full_ref = table_info.get("table", "")
        if not full_ref:
            continue

        # Skip self-references (handled separately by internal replacements) - but not
        # sibling references, which are replaced here with their ref() call
        if table_info.get("isSelfReference") and not table_info.get("isSiblingReference"):
            continue

        # Clean the reference
        clean_ref = full_ref.replace("`", "").replace('"', "")
        parts = clean_ref.split(".")

        if table_info.get("isSiblingReference"):
            # A table created by a sibling query in the same conversion set: it's a
            # model in the same dbt project, so reference it, don't source() it
            replacement = table_info.get("suggestedRef", "")
        elif len(parts) >= 2:
            # Determine replacement based on cross-project decision
            dataset_table_key = f"{parts[-2]}.{parts[-1]}"
            decision = cross_project_decisions.get(dataset_table_key)

            if decision and decision.get("use_cross_ref"):
                # Use cross-project ref
                project = decision.get("project", "")
                model = decision.get("model", "")
                replacement = f"{{{{ ref('{project}', '{model}') }}}}"
            else:
                # Use source() call
                replacement = table_info.get("suggestedSource", "")
        else:
            replacement = table_info.get("suggestedSource", "")

        if not replacement:
            continue

        # Add all matching variants to lookup
        # 1. Full name: project.dataset.table
        lookup_table[clean_ref.lower()] = (clean_ref, replacement, "full")

        # 2. Dataset.table
        if len(parts) >= 2:
            dataset_table = f"{parts[-2]}.{parts[-1]}"
            lookup_table[dataset_table.lower()] = (dataset_table, replacement, "dataset.table")

        # 3. Table only
        if len(parts) >= 1:
            table_only = parts[-1]
            # Only add if not already in lookup (avoid conflicts)
            if table_only.lower() not in lookup_table:
                lookup_table[table_only.lower()] = (table_only, replacement, "table")

    # Add internal replacements to lookup (these take precedence for simple names)
    for table_name, replacement in internal_replacements.items():
        lookup_table[table_name.lower()] = (table_name, replacement, "table")

    def transform_table(node: exp.Expression) -> exp.Expression:
        """Transform table references to use dbt source()/ref() syntax."""
        nonlocal placeholder_map

        if not isinstance(node, exp.Table):
            return node

        # Build the full qualified name from the AST node
        # sqlglot stores: catalog.db.this (project.dataset.table in BigQuery)
        parts = []
        if node.catalog:
            parts.append(node.catalog.name if hasattr(node.catalog, "name") else str(node.catalog))
        if node.db:
            parts.append(node.db.name if hasattr(node.db, "name") else str(node.db))
        if node.this:
            parts.append(node.this.name if hasattr(node.this, "name") else str(node.this))

        # Try matching from most specific to least specific
        matched_replacement = None
        matched_original = None

        # Try full qualified name first
        if len(parts) >= 3:
            full_name = ".".join(parts).lower()
            if full_name in lookup_table:
                matched_original, matched_replacement, _ = lookup_table[full_name]

        # Try dataset.table
        if not matched_replacement and len(parts) >= 2:
            dataset_table = ".".join(parts[-2:]).lower()
            if dataset_table in lookup_table:
                matched_original, matched_replacement, _ = lookup_table[dataset_table]

        # Try table only
        if not matched_replacement and len(parts) >= 1:
            table_only = parts[-1].lower()
            if table_only in lookup_table:
                matched_original, matched_replacement, _ = lookup_table[table_only]

        if not matched_replacement:
            return node

        # Track the replacement
        full_ref = node.sql(dialect="bigquery")
        replacements_made.append(f"{full_ref} → {matched_replacement}")

        # Create placeholder
        placeholder = f"__DBT_REF_{len(placeholder_map)}__"
        placeholder_map[placeholder] = matched_replacement

        # Preserve the table alias
        new_table = exp.Table(this=exp.Identifier(this=placeholder, quoted=False))
        if node.alias:
            new_table.set("alias", node.alias)

        return new_table

    try:
        # Protect comments before AST parsing (sqlglot can mangle them)
        protected_sql, comment_map = _protect_comments(sql)

        # Parse the SQL
        parsed = sqlglot.parse_one(protected_sql, dialect="bigquery")

        # Transform all table references
        transformed = parsed.transform(transform_table)

        # Generate the SQL back
        result = transformed.sql(dialect="bigquery", pretty=True)

        # Replace placeholders with actual source()/ref() calls
        for placeholder, replacement in placeholder_map.items():
            result = result.replace(placeholder, replacement)

        # Restore original comments
        result = _restore_comments(result, comment_map)

        # Prepend TODO comments for stripped statements
        if todo_comments:
            result = "\n".join(todo_comments) + "\n\n" + result

        return result, replacements_made, True

    except Exception as e:
        # If sqlglot fails, return original SQL
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"[sqlglot] Unified AST transform failed: {e}")
        return sql, [], False


def replace_table_references_ast(
    sql: str,
    table_replacements: dict[str, str],
) -> tuple[str, list[str]]:
    """
    Replace table references using sqlglot AST parsing for robustness.

    DEPRECATED: Use replace_all_table_references_unified() instead.
    This function is kept for backward compatibility.

    This is more reliable than regex because it:
    - Properly handles fully qualified names (project.dataset.table)
    - Handles backticks, quotes, and unquoted references
    - Only replaces actual table references (not strings, comments, etc.)

    Args:
        sql: The SQL content to transform
        table_replacements: Dict mapping short table names to replacement strings
            e.g., {"orders": "{{ ref('int__orders') }}"}

    Returns:
        Tuple of (transformed SQL, list of replacements made)
    """
    # Use the unified function
    result, replacements, success = replace_all_table_references_unified(
        sql=sql, internal_replacements=table_replacements
    )
    return result, replacements
