"""SQL parsing utilities for extracting metadata and analyzing queries."""

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dbt_training_wheels.utils.naming import get_case_style_and_separator, normalize_identifier
from dbt_training_wheels.utils.todo_comments import build_todo_comment

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import OrganizationConfig

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for better performance
_DECLARE_DEFAULT_PATTERN = re.compile(r"DECLARE\s+(\w+)\s+(\w+)\s+DEFAULT\s+([^;]+);", re.IGNORECASE)
# Updated to match ANY type, not just hardcoded list - catches custom types, new BigQuery types, etc.
_DECLARE_MULTI_PATTERN = re.compile(
    r"DECLARE\s+([\w\s,]+?)\s+(\w+)\s*;",
    re.IGNORECASE,
)
_SET_PATTERN = re.compile(r"SET\s+(\w+)\s*=\s*(\([^;]+\)|[^;]+);", re.IGNORECASE)
# Updated to match ANY type for cleanup as well
_DECLARE_CLEANUP_PATTERN = re.compile(
    r"DECLARE\s+[\w\s,]+\s+\w+(?:\s+DEFAULT\s+[^;]+)?;",
    re.IGNORECASE,
)
_SET_CLEANUP_PATTERN = re.compile(r"SET\s+\w+\s*=\s*(?:\([^;]+\)|[^;]+);", re.IGNORECASE)
_INSERT_COUNT_PATTERN = re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE)
_CREATE_COUNT_PATTERN = re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?(TABLE|VIEW)\b", re.IGNORECASE)
_INSERT_TABLE_PATTERN = re.compile(r'INSERT\s+INTO\s+[`"]?([a-zA-Z0-9_.-]+)[`"]?', re.IGNORECASE)
_CREATE_TABLE_PATTERN = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)\s+[`"]?([a-zA-Z0-9_.-]+)[`"]?', re.IGNORECASE
)
_CTE_DEPENDENCY_PATTERN = re.compile(r"(?:FROM|JOIN)\s+`?([a-zA-Z0-9_.-]+)`?", re.IGNORECASE)

# Import sqlglot-based parser for DECLARE/SET handling and statement extraction
# Type hints for optional sqlglot functions
_sqlglot_transform_vars: Callable[[str], str] | None = None
_sqlglot_extract_sql: Callable[[str, str], str | None] | None = None

try:
    from .sqlglot_parser import SQLGLOT_AVAILABLE

    # from .sqlglot_parser import extract_declared_variables as _sqlglot_extract_vars
    from .sqlglot_parser import extract_sql_for_table_sqlglot as _sqlglot_extract_sql
    from .sqlglot_parser import transform_variables_to_ctes as _sqlglot_transform_vars
except ImportError:
    SQLGLOT_AVAILABLE = False


def _remove_sql_comment_backticks(sql: str) -> str:
    """Remove commented out backticks from sql"""
    inline_pattern = re.compile(r"--.*?$", flags=re.MULTILINE)
    multiline_pattern = re.compile(r"/\*.*?\*/", flags=re.DOTALL)
    sql = re.sub(inline_pattern, _remove_backticks, sql)
    sql = re.sub(multiline_pattern, _remove_backticks, sql)
    return sql


def _remove_backticks(comment):
    return comment.group().replace("`", "")


def _strip_outer_parentheses(sql: str) -> str:
    """
    Strip outer parentheses if the entire SQL is wrapped in them.

    BigQuery allows: CREATE TABLE ... AS (WITH cte AS (...) SELECT ...)
    We need to remove the outer parens to get valid dbt SQL.

    Args:
        sql: SQL content that may be wrapped in parentheses

    Returns:
        SQL with outer parentheses removed if they wrap the entire statement
    """
    # Comment backticks can cause issues with the parser
    sql = _remove_sql_comment_backticks(sql).strip()

    # Check if starts with ( and ends with )
    if not (sql.startswith("(") and sql.endswith(")")):
        return sql

    # Count parentheses to ensure they match (the outer ones wrap everything)
    depth = 0
    for i, char in enumerate(sql):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            # If depth reaches 0 before the end, the parens don't wrap everything
            if depth == 0 and i < len(sql) - 1:
                return sql

    # The outer parentheses wrap the entire statement - remove them
    return sql[1:-1].strip()


def extract_declare_variables(sql: str) -> list[dict]:
    """
    Extract DECLARE variable statements from BigQuery SQL.

    Uses sqlglot for proper AST-based parsing if available, with regex fallback.

    Handles multiple patterns:
    1. DECLARE var1, var2 type;  (multiple variables, no default)
    2. DECLARE var type DEFAULT value;  (single variable with default)
    3. SET var = (SELECT...);  (value set separately)

    Args:
        sql: The SQL content to parse

    Returns:
        List of dicts with variable info:
        - 'variable': Variable name
        - 'type': Data type
        - 'defaultValue': Default value expression (from DEFAULT or SET)
    """
    # NOTE: We use regex instead of sqlglot for DECLARE extraction because:
    # 1. Sqlglot only recognizes standard BigQuery types (DATE, STRING, etc.)
    # 2. Custom types or new BigQuery types cause sqlglot to skip variables silently
    # 3. Our regex patterns are comprehensive and handle all type names
    # This ensures we detect ALL variables regardless of type name

    # Regex-based extraction
    variables = []
    seen_vars = {}

    # Pattern 1: DECLARE var type DEFAULT value;
    for match in _DECLARE_DEFAULT_PATTERN.finditer(sql):
        var_name = match.group(1)
        variables.append({"variable": var_name, "type": match.group(2).upper(), "defaultValue": match.group(3).strip()})
        seen_vars[var_name] = len(variables) - 1

    # Pattern 2: DECLARE var1, var2, ... type;  (multiple variables, no default)
    for match in _DECLARE_MULTI_PATTERN.finditer(sql):
        var_list = match.group(1)
        var_type = match.group(2).upper()
        var_names = [v.strip() for v in var_list.split(",") if v.strip()]
        for var_name in var_names:
            if var_name not in seen_vars:
                variables.append({"variable": var_name, "type": var_type, "defaultValue": None})
                seen_vars[var_name] = len(variables) - 1

    # Pattern 3: SET var = (SELECT...) or SET var = expression;
    for match in _SET_PATTERN.finditer(sql):
        var_name = match.group(1)
        value = match.group(2).strip()
        if var_name in seen_vars:
            variables[seen_vars[var_name]]["defaultValue"] = value
        else:
            variables.append({"variable": var_name, "type": "UNKNOWN", "defaultValue": value})
            seen_vars[var_name] = len(variables) - 1

    return variables


def extract_destination_datasets(sql: str) -> dict[str, dict[str, str]]:
    """
    Map each table the script writes to onto the project/dataset it is written to.

    CREATE and INSERT targets are the only place a destination dataset appears, and
    the rest of the pipeline reduces them to short names early on. This keeps the
    qualified form available for domain attribution.

    Args:
        sql: The full SQL content

    Returns:
        Dict keyed by short table name, each value holding 'fullName', 'project'
        and 'dataset' (empty strings when the reference isn't qualified).
    """
    destinations: dict[str, dict[str, str]] = {}

    for full_name in _CREATE_TABLE_PATTERN.findall(sql) + _INSERT_TABLE_PATTERN.findall(sql):
        parts = full_name.split(".")
        short_name = parts[-1]

        # First write wins - a table recreated later in the script keeps its original target
        if short_name in destinations:
            continue

        destinations[short_name] = {
            "fullName": full_name,
            "project": parts[-3] if len(parts) >= 3 else "",
            "dataset": parts[-2] if len(parts) >= 2 else "",
        }

    return destinations


def find_conflicting_table_basenames(sql: str) -> dict[str, list[str]]:
    """
    Find short table names written to by two genuinely different tables.

    The whole pipeline keys models on the short table name (extraction, layer
    classification, model naming), so `proj.customer_mart.base` and
    `proj.scratch.base` in one script would collapse into a single model built from
    whichever statement comes first. This detects that before it can happen.

    Not a conflict:
    - the same table written twice (CREATE then INSERT INTO the same target)
    - the same table referenced with different qualification - `ds.x` is treated as
      the same table as `proj.ds.x` (a dot-suffix of it)
    - unqualified writes (`INSERT INTO x`), which can't prove a different target

    Args:
        sql: The full SQL content

    Returns:
        Dict mapping each conflicting short name to the distinct qualified names
        that claim it. Empty when there are no conflicts.
    """
    qualified: dict[str, set[str]] = {}

    for full_name in _CREATE_TABLE_PATTERN.findall(sql) + _INSERT_TABLE_PATTERN.findall(sql):
        parts = full_name.lower().split(".")
        if len(parts) < 2:
            continue  # Unqualified writes can't prove a different target
        qualified.setdefault(parts[-1], set()).add(".".join(parts))

    conflicts: dict[str, list[str]] = {}
    for short_name, names in qualified.items():
        # Collapse names that are a dot-suffix of a longer one - 'ds.x' and
        # 'proj.ds.x' are the same table seen with different qualification
        distinct = [name for name in names if not any(other != name and other.endswith("." + name) for other in names)]
        if len(distinct) > 1:
            conflicts[short_name] = sorted(distinct)

    return conflicts


def find_recreated_tables(sql: str) -> dict[str, int]:
    """
    Find tables built by more than one CREATE ... AS statement in the same script.

    Distinct from find_conflicting_table_basenames: the target is the *same* table, so
    it isn't a naming collision. It's still broken, and worse, because the two readers
    disagree - extraction takes the first CREATE while BigQuery would run the last, so
    the generated model may not be the SQL that actually built the table. Merging two
    files that each recreate a table is the usual way this happens.

    CREATE followed by INSERT INTO the same target is the normal build-then-append
    pattern and is not reported.

    Args:
        sql: The full SQL content

    Returns:
        Dict mapping each qualified table name to how many times it is created,
        for tables created more than once. Empty when there are none.
    """
    counts: dict[str, int] = {}
    for full_name in _CREATE_TABLE_PATTERN.findall(sql):
        counts[full_name.lower()] = counts.get(full_name.lower(), 0) + 1

    return {name: count for name, count in counts.items() if count > 1}


def extract_cte_models(sql: str) -> list[dict]:
    """
    Extract CTEs with their SQL and basic dependencies.

    Returns:
        List of dicts with:
        - name: CTE name
        - sql: CTE SQL body
        - dependencies: list of table refs found in FROM/JOIN
    """
    cte_models: list[dict] = []

    if SQLGLOT_AVAILABLE:
        try:
            import sqlglot
            from sqlglot import exp

            statements = sqlglot.parse(sql, dialect="bigquery")
            for stmt in statements:
                if stmt is None:
                    continue
                with_expr = stmt.find(exp.With)
                if not with_expr:
                    continue
                for cte in with_expr.expressions:
                    name = cte.alias_or_name
                    if not name:
                        continue
                    cte_query = cte.this
                    cte_sql = cte_query.sql(dialect="bigquery") if cte_query and hasattr(cte_query, "sql") else ""
                    dependencies = []
                    if cte_query:
                        for table in cte_query.find_all(exp.Table):
                            table_sql = table.sql(dialect="bigquery")
                            if table_sql:
                                # Strip alias (e.g., "table AS t" -> "table")
                                table_sql_clean = table_sql
                                if " AS " in table_sql.upper():
                                    # Case-insensitive split
                                    idx = table_sql.upper().index(" AS ")
                                    table_sql_clean = table_sql[:idx].strip()
                                dependencies.append(table_sql_clean)
                    cte_models.append({"name": name, "sql": cte_sql, "dependencies": dependencies})
            return cte_models
        except Exception:
            # Fall back to regex if sqlglot fails
            cte_models = []

    # Regex fallback: parse WITH ... CTE blocks with a simple parenthesis counter
    sql_upper = sql.upper()
    with_match = re.search(r"\bWITH\b", sql_upper)
    if not with_match:
        return cte_models

    i = with_match.end()
    length = len(sql)
    while i < length:
        # Skip whitespace and commas
        while i < length and sql[i] in " \t\n\r,":
            i += 1
        if i >= length:
            break

        # Read CTE name
        name_start = i
        while i < length and (sql[i].isalnum() or sql[i] == "_"):
            i += 1
        name = sql[name_start:i].strip()
        if not name:
            break

        # Skip whitespace
        while i < length and sql[i].isspace():
            i += 1

        # Expect AS
        if sql_upper[i : i + 2] != "AS":
            break
        i += 2

        # Skip whitespace
        while i < length and sql[i].isspace():
            i += 1

        # Expect opening paren
        if i >= length or sql[i] != "(":
            break

        # Capture balanced parentheses
        depth = 0
        sql_start = i + 1
        i += 1
        while i < length:
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                if depth == 0:
                    cte_sql = sql[sql_start:i].strip()
                    dependencies = _CTE_DEPENDENCY_PATTERN.findall(cte_sql)
                    cte_models.append({"name": name, "sql": cte_sql, "dependencies": dependencies})
                    i += 1
                    break
                depth -= 1
            i += 1

        # Check if next token continues CTE list
        while i < length and sql[i].isspace():
            i += 1
        if i < length and sql[i] == ",":
            i += 1
            continue
        break

    return cte_models


def extract_final_select_source(sql: str) -> str | None:
    """
    Extract the table/CTE name from the main query's FROM clause (after all CTEs).

    For SQL like:
        WITH cte1 AS (...), cte2 AS (...)
        SELECT * FROM cte2

    Or INSERT statements:
        INSERT INTO table
        WITH cte1 AS (...)
        SELECT * FROM cte1

    Returns: "cte2" or "cte1"

    This is used to determine which intermediate model a mart should reference.

    Returns:
        The table/CTE name from the final SELECT's FROM clause, or None if not found.
    """
    if SQLGLOT_AVAILABLE:
        try:
            import sqlglot
            from sqlglot import exp

            statements = sqlglot.parse(sql, dialect="bigquery")
            for stmt in statements:
                if stmt is None:
                    continue

                # Handle INSERT statements - find the SELECT within
                if isinstance(stmt, exp.Insert):
                    select_expr = stmt.find(exp.Select)
                    if select_expr:
                        from_clause = select_expr.find(exp.From)
                        if from_clause:
                            table = from_clause.find(exp.Table)
                            if table and table.name:
                                return str(table.name)

                # Handle direct SELECT with WITH clause
                elif isinstance(stmt, exp.Select):
                    from_clause = stmt.find(exp.From)
                    if from_clause:
                        table = from_clause.find(exp.Table)
                        if table and table.name:
                            return str(table.name)

            return None
        except Exception as e:
            logger.debug(f"sqlglot extract_final_select_source failed: {e}")
            # Fall back to regex

    # Regex fallback: find the final SELECT ... FROM <table>
    # Skip past the WITH clause by finding the last SELECT
    sql_upper = sql.upper()

    # Find the position after the last CTE (after the WITH ... AS (...) block)
    # Look for SELECT that's NOT inside a CTE
    with_match = re.search(r"\bWITH\b", sql_upper)
    if with_match:
        # Find the main query SELECT (after all CTEs end)
        # Count parentheses to skip CTE bodies
        i = with_match.end()
        length = len(sql)
        paren_depth = 0
        in_cte = False

        while i < length:
            char = sql[i]
            if char == "(":
                paren_depth += 1
                in_cte = True
            elif char == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    in_cte = False
            elif not in_cte and paren_depth == 0:
                # Check for SELECT keyword
                if sql_upper[i : i + 6] == "SELECT":
                    # Found the main SELECT, now find FROM
                    from_match = re.search(r"\bFROM\s+`?([a-zA-Z0-9_]+)`?", sql_upper[i:])
                    if from_match:
                        # Get the actual case from original SQL
                        actual_start = i + from_match.start(1)
                        actual_end = i + from_match.end(1)
                        return sql[actual_start:actual_end].strip().strip("`")
                    break
            i += 1
    else:
        # No WITH clause, just find FROM in the main SELECT
        from_match = re.search(r"\bFROM\s+`?([a-zA-Z0-9_]+)`?", sql_upper)
        if from_match:
            return sql[from_match.start(1) : from_match.end(1)].strip().strip("`")

    return None


def transform_declare_to_cte(sql: str, variables: list[dict[str, Any]] | None = None) -> str:
    """
    Transform DECLARE/SET statements into CTEs for dbt compatibility.

    Uses sqlglot for proper AST-based parsing if available, with regex fallback.

    BigQuery DECLARE/SET statements are not supported in dbt models.
    This function converts them to CTEs that can be referenced.

    For example:
        DECLARE max_report_date date;
        SET max_report_date = (SELECT MAX(report_date) FROM table);
        ... WHERE date = max_report_date ...

    Becomes:
        WITH __var_max_report_date AS (SELECT MAX(report_date) as value FROM table)
        ... WHERE date = (SELECT value FROM __var_max_report_date) ...

    Args:
        sql: The SQL content with DECLARE/SET statements
        variables: Optional pre-extracted variables (if None, will extract)

    Returns:
        Transformed SQL with CTEs instead of DECLARE/SET
    """
    # Use sqlglot if available for better parsing
    if SQLGLOT_AVAILABLE and _sqlglot_transform_vars:
        try:
            return _sqlglot_transform_vars(sql)
        except Exception as e:
            logger.debug(f"sqlglot transform failed, falling back to regex: {e}")
            # Fall back to regex

    # Regex fallback
    if variables is None:
        variables = extract_declare_variables(sql)

    if not variables:
        return sql

    transformed = sql

    # First, remove all DECLARE statements
    transformed = _DECLARE_CLEANUP_PATTERN.sub("", transformed)

    # Remove all SET statements
    transformed = _SET_CLEANUP_PATTERN.sub("", transformed)

    # Remove the standalone SELECT that often follows DECLARE/SET for debugging
    debug_select_pattern = r"SELECT\s+[\w\s,]+;(?=\s*[-/])"
    transformed = re.sub(debug_select_pattern, "", transformed, flags=re.IGNORECASE)

    # Build CTEs for variables that have subquery values
    var_replacements = {}
    for var in variables:
        var_name = var["variable"]
        default_value = var.get("defaultValue", "")

        if default_value and default_value.strip().startswith("("):
            cte_name = f"__var_{var_name}"
            var_replacements[var_name] = f"(SELECT * FROM {cte_name})"
        elif default_value:
            var_replacements[var_name] = f"({default_value})"

    # Build CTEs with variable references resolved
    ctes_to_add = []
    for var in variables:
        var_name = var["variable"]
        default_value = var.get("defaultValue", "")

        if default_value and default_value.strip().startswith("("):
            subquery = default_value.strip()
            if subquery.startswith("(") and subquery.endswith(")"):
                subquery = subquery[1:-1].strip()

            # Replace any variable references within this subquery
            for other_var, replacement in var_replacements.items():
                if other_var != var_name:
                    var_pattern = rf"\b{re.escape(other_var)}\b"
                    subquery = re.sub(var_pattern, replacement, subquery)

            cte_name = f"__var_{var_name}"
            ctes_to_add.append(f"{cte_name} AS ({subquery})")

    # Replace variable usages in the main SQL
    for var_name, replacement in var_replacements.items():
        var_pattern = rf"\b{re.escape(var_name)}\b"
        transformed = re.sub(var_pattern, replacement, transformed)

    # Add CTEs to the SQL
    if ctes_to_add:
        transformed = transformed.strip()
        lines = transformed.split("\n")
        cleaned_lines = []
        found_content = False
        for line in lines:
            if line.strip() or found_content:
                found_content = True
                cleaned_lines.append(line)
        transformed = "\n".join(cleaned_lines)

        if re.match(r"^\s*WITH\s+", transformed, re.IGNORECASE):
            cte_prefix = ",\n    ".join(ctes_to_add)
            transformed = re.sub(r"^(\s*WITH\s+)", rf"\1{cte_prefix},\n    ", transformed, count=1, flags=re.IGNORECASE)
        else:
            cte_block = "WITH " + ",\n    ".join(ctes_to_add) + "\n\n"
            transformed = cte_block + transformed

    transformed = re.sub(r"\n{3,}", "\n\n", transformed)

    return transformed.strip()


def parse_sql_file(filepath: str, config: "OrganizationConfig | None" = None) -> dict[str, Any]:
    """
    Parse a SQL file and extract metadata from comments and content.

    Args:
        filepath: Path to the SQL file
        config: Optional OrganizationConfig for customizing parsing behavior

    Returns:
        Dict containing parsed metadata and SQL content
    """
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    default_name = Path(filepath).stem.replace("_", " ").title()
    # Get metadata patterns from config or use defaults
    metadata_patterns = None
    if config and config.parser:
        metadata_patterns = config.parser.metadata_patterns

    # Extract metadata from comments (optional)
    metadata: dict[str, Any] = {"name": None, "dataset": None, "schedule": "On Demand", "tables": []}

    lines = content.split("\n")
    for line in lines[:30]:  # Check first 30 lines for metadata
        line = line.strip()

        # Use config patterns if available, otherwise use defaults
        if metadata_patterns:
            for key, pattern in metadata_patterns.items():
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    metadata[key] = match.group(1).strip()
        else:
            # Default patterns
            if line.startswith("-- name:"):
                metadata["name"] = line.replace("-- name:", "").strip()
            elif line.startswith("-- dataset:"):
                metadata["dataset"] = line.replace("-- dataset:", "").strip()
            elif line.startswith("-- schedule:") or line.startswith("-- Schedule:"):
                schedule_line = line.replace("-- schedule:", "").replace("-- Schedule:", "").strip()
                metadata["schedule"] = schedule_line
            elif line.startswith("-- tables:"):
                tables_str = line.replace("-- tables:", "").strip()
                metadata["tables"] = [t.strip() for t in tables_str.split(",") if t.strip()]

    # If name not provided, use default_name (always works)
    if not metadata["name"]:
        metadata["name"] = default_name

    # Count INSERT and CREATE statements (auto-detect)
    insert_count = len(_INSERT_COUNT_PATTERN.findall(content))
    create_count = len(_CREATE_COUNT_PATTERN.findall(content))

    # ALWAYS auto-detect tables from CREATE/INSERT statements (ignore metadata tables)
    # Try to find table names in INSERT INTO statements
    insert_tables = _INSERT_TABLE_PATTERN.findall(content)
    # Try to find table names in CREATE TABLE/VIEW statements
    create_tables = _CREATE_TABLE_PATTERN.findall(content)

    # Extract just the table name (last part after dots)
    all_detected_tables = insert_tables + create_tables
    detected_table_names = [t.split(".")[-1] for t in all_detected_tables if t]

    # Use detected tables (always), not metadata
    metadata["tables"] = detected_table_names

    return {
        **metadata,
        "sql": content,
        "insertCount": max(insert_count, create_count, len(detected_table_names) if detected_table_names else 1),
    }


def extract_sql_for_table(sql: str, table_name: str) -> str | None:
    """
    Extract the SQL logic for a specific table from CREATE/INSERT statements.
    Returns the SELECT portion or WITH...SELECT for dbt model.

    Uses sqlglot for proper AST-based extraction when available, falls back to regex.

    Args:
        sql: The full SQL content
        table_name: Name of the table to extract SQL for

    Returns:
        Extracted SQL content or None if not found
    """
    # Try sqlglot first for reliable statement separation
    if SQLGLOT_AVAILABLE and _sqlglot_extract_sql:
        try:
            result = _sqlglot_extract_sql(sql, table_name)
            if result:
                return result
        except Exception as e:
            logger.debug(f"sqlglot extraction failed for table {table_name}, falling back to regex: {e}")
            # Fall back to regex

    # Regex fallback
    # Pattern 1: CREATE TABLE/VIEW statement
    # Matches: CREATE OR REPLACE TABLE/VIEW `project.dataset.table_name` AS
    # Uses lookahead to stop before the next CREATE/INSERT/MERGE statement
    # Updated to handle cases where statements aren't separated by semicolons
    create_pattern = rf'CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)\s+[`"]?[a-zA-Z0-9_.-]*\.?[a-zA-Z0-9_.-]*\.?{re.escape(table_name)}[`"]?\s+(?:AS|as)\s+([\s\S]*?)(?:;\s*)?(?=(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|TEMP|TEMPORARY|EXTERNAL)|INSERT\s+INTO|MERGE\s+|DELETE\s+|UPDATE\s+)|$)'

    match = re.search(create_pattern, sql, re.IGNORECASE)

    if match:
        # Extract everything after AS
        sql_content = match.group(1).strip()
        # Clean up the SQL - remove trailing semicolons and comments
        sql_content = sql_content.rstrip(";").strip()
        # Remove trailing SQL comments that might have been captured
        # This handles cases where comments at the end run into the next statement
        sql_content = re.sub(r"(\n\s*--[^\n]*)+\s*$", "", sql_content).strip()
        # Strip outer parentheses if the entire SQL is wrapped in them
        sql_content = _strip_outer_parentheses(sql_content)
        return sql_content

    # Pattern 2: INSERT INTO statement
    # Matches: INSERT INTO `project.dataset.table_name` WITH... or SELECT...
    # Uses lookahead to stop at semicolon followed by any whitespace/comments then another statement
    # Updated to handle cases where statements aren't separated by semicolons
    insert_pattern = rf'INSERT\s+INTO\s+[`"]?[a-zA-Z0-9_.-]*\.?[a-zA-Z0-9_.-]*\.?{re.escape(table_name)}[`"]?\s+((?:WITH|SELECT)[\s\S]*?)(?:;\s*)?(?=(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|TEMP|TEMPORARY|EXTERNAL)|INSERT\s+INTO|MERGE\s+|DELETE\s+|UPDATE\s+)|$)'

    match = re.search(insert_pattern, sql, re.IGNORECASE)

    if match:
        # Extract everything after INSERT INTO table_name
        sql_content = match.group(1).strip()
        # Clean up the SQL - remove trailing semicolons and whitespace
        sql_content = re.sub(r";\s*$", "", sql_content).strip()
        # Remove trailing SQL comments that might have been captured
        sql_content = re.sub(r"(\n\s*--[^\n]*)+\s*$", "", sql_content).strip()
        return sql_content

    # If no match found, return None
    return None


def extract_standalone_select(sql: str) -> str | None:
    """
    Extract SQL from standalone SELECT or WITH...SELECT queries
    (queries that don't use CREATE TABLE or INSERT INTO).

    This handles:
    - Simple SELECT ... FROM queries
    - WITH cte AS (...) SELECT ... queries
    - Nested subquery patterns

    Args:
        sql: The full SQL content

    Returns:
        Extracted SQL content or None if not a standalone query
    """
    # Remove metadata comments at the top (lines starting with --)
    lines = sql.split("\n")
    sql_lines = []
    in_sql = False

    for line in lines:
        stripped = line.strip()
        # Skip empty lines and comment lines at the start
        if not in_sql:
            if stripped and not stripped.startswith("--"):
                in_sql = True
                sql_lines.append(line)
        else:
            sql_lines.append(line)

    cleaned_sql = "\n".join(sql_lines).strip()

    # Check if this is NOT a CREATE/INSERT statement
    if re.match(r"^\s*(CREATE|INSERT)\s+", cleaned_sql, re.IGNORECASE):
        return None

    # Pattern 3: WITH...SELECT (CTE pattern)
    with_pattern = r"^(WITH\s+.*?SELECT\s+.*?)(?:;|\Z)"
    match = re.search(with_pattern, cleaned_sql, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).rstrip(";").strip()

    # Pattern 4: Standalone SELECT (including nested subqueries)
    select_pattern = r"^(SELECT\s+.*?)(?:;|\Z)"
    match = re.search(select_pattern, cleaned_sql, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).rstrip(";").strip()

    return None


def detect_sql_type(sql: str) -> str:
    """
    Detect the type of SQL query.

    Args:
        sql: The SQL content

    Returns:
        One of: 'create_table', 'insert_into', 'standalone_select', 'with_cte', 'merge', 'other'
    """
    # Remove comments
    lines = [line for line in sql.split("\n") if not line.strip().startswith("--")]
    cleaned = "\n".join(lines).strip()

    if re.match(r"^\s*CREATE\s+", cleaned, re.IGNORECASE):
        return "create_table"
    elif re.match(r"^\s*INSERT\s+INTO\s+", cleaned, re.IGNORECASE):
        return "insert_into"
    elif re.match(r"^\s*WITH\s+", cleaned, re.IGNORECASE):
        return "with_cte"
    elif re.match(r"^\s*MERGE\s+", cleaned, re.IGNORECASE):
        return "merge"
    elif re.match(r"^\s*SELECT\s+", cleaned, re.IGNORECASE):
        return "standalone_select"
    else:
        return "other"


def analyze_sql_content(
    sql: str,
    config: "OrganizationConfig | None" = None,
    project_name: str | None = None,
    sibling_tables: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Analyze SQL content for CREATE statements.

    Logic for CREATE statements:
    - Each CREATE TABLE gets its own final model
    - CTEs stay within their final model (no extraction)
    - ONLY create prep models if a created table is referenced later

    Args:
        sql: The SQL content to analyze
        config: Optional OrganizationConfig for customizing naming and detection
        project_name: Optional project name for project-specific configuration
        sibling_tables: Short names of tables created by sibling queries in the same
            uploaded folder. References to them become ref() calls (they're models in
            the same dbt project), not source() calls.

    Returns:
        Tuple of (prep_models, hardcoded_tables)
    """

    def _sanitize_identifier(value: str) -> str:
        return normalize_identifier(value, case_style=case_style, separator=separator)

    # Initialize with schema defaults (will be overridden by config if available)
    dbt_model_keywords = ["analytics", "dwh_", "dim_", "fact_"]
    max_prep_models = 3
    case_style, separator = get_case_style_and_separator(config, project_name)
    sibling_lookup = {name.lower() for name in (sibling_tables or set())}

    # Get naming values from config (project-specific takes precedence)
    if config and project_name and config.projects and project_name in config.projects:
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"[SQL Parser] Using project-specific config for: {project_name}")

        project_config = config.projects[project_name]
        if project_config.dbt_config and project_config.dbt_config.naming:
            project_naming = project_config.dbt_config.naming
            staging_model_prefix = project_naming.staging_model_prefix
            intermediate_model_prefix = project_naming.intermediate_model_prefix
            source_name_from = project_naming.source_name_from
            include_schema_in_model_name = project_naming.include_schema_in_model_name
        elif config and config.naming:
            # Project exists but no naming config - use org-level naming
            staging_model_prefix = config.naming.staging_model_prefix
            intermediate_model_prefix = config.naming.intermediate_model_prefix
            source_name_from = config.naming.source_name_from
            include_schema_in_model_name = config.naming.include_schema_in_model_name
        else:
            # No config at all - use schema defaults
            from dbt_training_wheels.config_schema import ModelNamingConfig

            defaults = ModelNamingConfig()
            staging_model_prefix = defaults.staging_model_prefix
            intermediate_model_prefix = defaults.intermediate_model_prefix
            source_name_from = defaults.source_name_from
            include_schema_in_model_name = defaults.include_schema_in_model_name
    elif config and config.naming:
        # No project name provided - use org-level naming
        staging_model_prefix = config.naming.staging_model_prefix
        intermediate_model_prefix = config.naming.intermediate_model_prefix
        source_name_from = config.naming.source_name_from
        include_schema_in_model_name = config.naming.include_schema_in_model_name
    else:
        # No config at all - use schema defaults
        from dbt_training_wheels.config_schema import ModelNamingConfig

        defaults = ModelNamingConfig()
        staging_model_prefix = defaults.staging_model_prefix
        intermediate_model_prefix = defaults.intermediate_model_prefix
        source_name_from = defaults.source_name_from
        include_schema_in_model_name = defaults.include_schema_in_model_name

    if config and config.parser:
        max_prep_models = config.parser.max_cte_models
        # Could add dbt_model_keywords to config in future

    # Find all CREATE TABLE statements with full qualified name
    create_pattern = r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+`?([a-zA-Z0-9_.-]+)`?"
    created_tables_full = re.findall(create_pattern, sql, re.IGNORECASE)

    # Also find INSERT INTO statements (tables being written to are also "created" in the context of this script)
    insert_pattern = r"INSERT\s+INTO\s+`?([a-zA-Z0-9_.-]+)`?"
    inserted_tables_full = re.findall(insert_pattern, sql, re.IGNORECASE)

    # Combine both CREATE and INSERT tables
    all_created_tables = created_tables_full + inserted_tables_full

    # Extract just table names (last part after dots)
    created_table_names = [t.split(".")[-1] for t in all_created_tables]

    # Find tables that are referenced in FROM/JOIN clauses AFTER they're created
    # This is for detecting reuse within the same script
    reference_pattern = r"(?:FROM|JOIN)\s+`?([a-zA-Z0-9_.-]+)`?"
    referenced_tables = re.findall(reference_pattern, sql, re.IGNORECASE)
    referenced_table_names = [t.split(".")[-1] for t in referenced_tables]

    # Find which created tables are reused (appear in FROM/JOIN after being created)
    reused_tables = []
    for created_table in created_table_names:
        # Count how many times this table appears in FROM/JOIN
        # If it appears, it means it's being reused
        if referenced_table_names.count(created_table) > 0:
            # Need to check if it appears AFTER its CREATE statement
            # For now, simple check: if it's in references, it might be reused
            # We'll need to verify it's not just in a nested subquery

            # Extract the SQL for this table (for SCS calculation in layer classification)
            table_sql = extract_sql_for_table(sql, created_table)

            reused_tables.append(
                {
                    "name": created_table,
                    "canBeReused": True,
                    "description": "Table created and reused within the script",
                    "sql": table_sql or "",  # Include SQL for layer classification
                    "dependencies": [],  # Could extract dependencies in future
                }
            )

    # Limit prep models based on config
    prep_models = reused_tables[:max_prep_models]

    # Extract hardcoded source table references
    # Pattern 1: Backtick-quoted tables (e.g., `project.dataset.table`)
    hardcoded_pattern = r"`([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)`"
    all_backtick_refs = re.findall(hardcoded_pattern, sql)

    # Pattern 2: Unquoted fully-qualified tables in FROM/JOIN clauses
    # Matches: FROM project.dataset.table or FROM project-name.dataset.table
    # Also handles tables with hyphens in project names
    unquoted_pattern = r"(?:FROM|JOIN)\s+([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)"
    unquoted_refs = re.findall(unquoted_pattern, sql, re.IGNORECASE)

    # Combine both patterns
    all_table_refs = all_backtick_refs + unquoted_refs

    # Get full table names that are INSERT INTO or CREATE TABLE targets (destinations, not sources)
    # We use counts to determine if a created table is also used as a source
    created_table_counts: dict[str, int] = {}
    for t in all_created_tables:
        created_table_counts[t] = created_table_counts.get(t, 0) + 1

    table_ref_counts: dict[str, int] = {}
    for t in all_table_refs:
        table_ref_counts[t] = table_ref_counts.get(t, 0) + 1

    # Include ALL table references (both external AND self-references)
    # Self-references (tables created in this script and referenced later) should be converted to ref()
    seen_tables = set()
    table_list = []
    for table in all_table_refs:
        # Note: We do NOT skip destination_tables here because self-references need to be
        # converted to ref() calls. The extraction function already strips the CREATE/INSERT part.
        # Must have at least 2 dots (project.dataset.table format)
        if table.count(".") >= 2 and table not in seen_tables:
            # Check if this is ONLY a destination (created/inserted but never read)
            if table in created_table_counts:
                # If the number of times we found it as a reference is <= the number of times
                # we found it as a creation target, then it's only being created, not read.
                if table_ref_counts.get(table, 0) <= created_table_counts.get(table, 0):
                    continue

            table_name = table.split(".")[-1]
            parts = table.split(".")

            # Determine source name based on config
            if source_name_from == "dataset" and len(parts) >= 2:
                schema = parts[1] if len(parts) == 3 else parts[-2]
            elif source_name_from == "schema" and len(parts) >= 2:
                schema = parts[-2]
            else:
                schema = parts[1] if len(parts) == 3 else parts[-2]

            # Check if this is a self-reference (table created in same script)
            is_self_reference = table_name in created_table_names

            # Or a sibling reference: created by another query in the same uploaded
            # folder. Treated like a self-reference (ref(), excluded from sources.yml
            # and cross-project/scheduled-query detection) since it's a model in the
            # same dbt project.
            is_sibling_reference = not is_self_reference and table_name.lower() in sibling_lookup

            is_dbt_model = any(keyword in table.lower() for keyword in dbt_model_keywords)

            table_info = {
                "table": table,
                # Self- and sibling references are treated like dbt models
                "isDbtModel": is_dbt_model or is_self_reference or is_sibling_reference,
                "isSelfReference": is_self_reference or is_sibling_reference,
                "isSiblingReference": is_sibling_reference,
                "suggestedSource": f"{{{{ source('{schema}', '{table_name}') }}}}",
                "sourceSchema": schema,
                "sourceTable": table_name,
            }

            sanitized_schema = _sanitize_identifier(schema or "source")
            sanitized_table = _sanitize_identifier(table_name)
            # Build staging model name based on config (used for source tables)
            if include_schema_in_model_name:
                staging_model_name = f"{staging_model_prefix}{sanitized_schema}__{sanitized_table}"
            else:
                staging_model_name = f"{staging_model_prefix}{sanitized_table}"
            table_info["stagingModelName"] = staging_model_name  # Keep key name for backward compatibility
            table_info["suggestedStagingRef"] = f"{{{{ ref('{staging_model_name}') }}}}"

            if is_dbt_model or is_self_reference or is_sibling_reference:
                # Self- and sibling references should use ref() to the intermediate model
                is_internal = is_self_reference or is_sibling_reference
                normalized_table = _sanitize_identifier(table_name)
                ref_target = f"{intermediate_model_prefix}{normalized_table}" if is_internal else table_name
                table_info["suggestedRef"] = (
                    f"{{{{ ref('{ref_target}') }}}}"
                    if is_internal
                    else f"{{{{ ref('other_project', '{table_name}') }}}}"
                )

            table_list.append(table_info)
            seen_tables.add(table)

    return prep_models, table_list


def _transform_with_regex_fallback(
    sql: str,
    external_tables: list[dict],
    cross_project_decisions: dict[str, dict],
    todo_comments: list[dict],
) -> str:
    """
    Regex-based table replacement fallback (DEPRECATED - only used if AST fails).

    This is kept as a last-resort fallback if sqlglot AST parsing fails.
    Prefer the AST-based approach for robustness.

    Args:
        sql: SQL to transform
        external_tables: List of external table dicts
        cross_project_decisions: Cross-project decision mapping
        todo_comments: List to append TODO items to

    Returns:
        Transformed SQL
    """
    transformed = sql

    # Sort by length to avoid partial matches
    sorted_tables = sorted(external_tables, key=lambda t: len(t.get("table", "")), reverse=True)

    for table_info in sorted_tables:
        full_table_ref = table_info.get("table", "")
        if not full_table_ref:
            continue

        # Build lookup key
        parts = full_table_ref.replace("`", "").replace('"', "").split(".")
        if len(parts) >= 2:
            lookup_key = f"{parts[-2]}.{parts[-1]}"
        else:
            lookup_key = parts[-1]

        # Determine replacement
        decision = cross_project_decisions.get(lookup_key)
        if decision and decision.get("use_cross_ref"):
            project = decision.get("project", "")
            model = decision.get("model", "")
            if project and model:
                replacement = f"{{{{ ref('{project}', '{model}') }}}}"
            else:
                replacement = table_info.get("suggestedSource", "")
        else:
            replacement = table_info.get("suggestedSource", "")

        if not replacement:
            continue

        # Regex replacement with alias preservation
        clean_table_ref = full_table_ref.replace("`", "").replace('"', "")
        escaped_table = re.escape(clean_table_ref)
        pattern = rf"(?<![.\w])(?:`{escaped_table}`|\"{escaped_table}\"|{escaped_table})(\s+(?:AS\s+)?([a-zA-Z_][a-zA-Z0-9_]*))?"

        table_found = False

        def replace_with_alias(match, repl=replacement):
            """Bind replacement to avoid B023 closure issue."""
            alias = match.group(2)
            if alias:
                return f"{repl} {alias}"
            return repl

        if re.search(pattern, transformed, flags=re.IGNORECASE):
            transformed = re.sub(pattern, replace_with_alias, transformed, flags=re.IGNORECASE)
            table_found = True
        else:
            # Try short form
            if len(parts) >= 2:
                dataset_table = f"{parts[-2]}.{parts[-1]}"
                escaped_short = re.escape(dataset_table)
                pattern_short = rf"(?<![.\w])(?:`{escaped_short}`|\"{escaped_short}\"|{escaped_short})(\s+(?:AS\s+)?([a-zA-Z_][a-zA-Z0-9_]*))?"
                if re.search(pattern_short, transformed, flags=re.IGNORECASE):
                    transformed = re.sub(pattern_short, replace_with_alias, transformed, flags=re.IGNORECASE)
                    table_found = True

        # Collect TODO comments
        if table_found and table_info.get("isScheduledQueryDependency"):
            if "source(" in replacement:
                scheduled_project = table_info.get("scheduledQueryProject", "")
                if len(parts) >= 2:
                    table_display = f"{parts[-2]}.{parts[-1]}"
                else:
                    table_display = parts[-1] if parts else clean_table_ref

                todo_item = {"project": scheduled_project, "table": table_display, "full_ref": full_table_ref}
                todo_comments.append(todo_item)

    return transformed


def transform_sql_with_sources(
    sql: str,
    hardcoded_tables: list[dict],
    cross_project_decisions: dict[str, dict] | None = None,
    full_sql_declare_variables: list[dict] | None = None,
) -> str:
    """
    Transform SQL by replacing hardcoded table references with dbt source() and ref() calls.

    This is the critical function that converts raw SQL into dbt-ready SQL.

    Uses AST-based transformation via sqlglot for robustness, with regex fallback.

    Args:
        sql: The original SQL content with hardcoded table references
        hardcoded_tables: List of table info dicts from analyze_sql_content()
            Each dict should contain:
            - 'table': Full qualified table name (e.g., 'project.dataset.table')
            - 'suggestedSource': The source() call to use
            - 'suggestedRef': The ref() call to use (for self-references)
            - 'isSelfReference': Boolean flag
        cross_project_decisions: Optional dict mapping "dataset.table" to decision dict
            Each decision dict should contain:
            - 'use_cross_ref': Boolean - whether to use cross-project ref
            - 'project': Project name for cross-project ref
            - 'model': Model name for cross-project ref
        full_sql_declare_variables: Optional list of DECLARE variables from the full SQL file.
            If provided, these are used instead of extracting from the current SQL.
            This is useful when processing extracted table SQL that references variables
            declared at the file level.

    Returns:
        Transformed SQL with source() and ref() calls replacing hardcoded tables
    """
    if not hardcoded_tables:
        return sql

    if cross_project_decisions is None:
        cross_project_decisions = {}

    logger.debug(
        f"[SQL Transform] Loaded {len(cross_project_decisions)} cross-project decisions: {list(cross_project_decisions.keys())}"
    )

    # Handle DECLARE variables: use provided variables or extract from SQL
    if full_sql_declare_variables is not None:
        # Variables were passed from full SQL - use these for informational notes
        # These variables might not actually be in the extracted SQL text, but are referenced
        original_declare_variables = full_sql_declare_variables
        # Since variables are declared elsewhere (top of full file), we don't try to convert them
        transformed = sql
        remaining_declare_variables = []  # They're not in this extracted SQL to convert
    else:
        # No variables passed - extract from current SQL and attempt auto-conversion
        original_declare_variables = extract_declare_variables(sql)
        # Attempt auto-transformation
        transformed = transform_declare_to_cte(sql) if original_declare_variables else sql
        # Check if any DECLARE variables remain after transformation
        remaining_declare_variables = extract_declare_variables(transformed) if original_declare_variables else []

    # Collect TODO comments for top-of-file section
    todo_comments = []

    # ============================================================================
    # AST-BASED TABLE REPLACEMENT - Primary approach
    # ============================================================================
    # Try AST-based transformation first (robust, handles all edge cases)
    from dbt_training_wheels.utils.sqlglot_parser import replace_all_table_references_unified

    # Filter to only replaceable tables: externals plus sibling references (which
    # become ref() calls). True self-references are handled by the CTE ref transform.
    external_tables = [t for t in hardcoded_tables if not t.get("isSelfReference") or t.get("isSiblingReference")]

    logger.info(f"[SQL Transform] Using AST-based transformation for {len(external_tables)} external tables")

    transformed_ast, replacements_made, ast_success = replace_all_table_references_unified(
        sql=transformed, external_tables=external_tables, cross_project_decisions=cross_project_decisions
    )

    if ast_success and replacements_made:
        logger.info(f"[SQL Transform] ✓ AST successfully transformed {len(replacements_made)} table references")
        for replacement_info in replacements_made:
            logger.debug(f"  - {replacement_info}")
        transformed = transformed_ast

        # Collect TODO comments for scheduled query dependencies
        # Check which tables were replaced and collect TODOs
        for table_info in external_tables:
            if table_info.get("isScheduledQueryDependency"):
                # Check if this table was replaced with source() (not ref())
                suggested_source = table_info.get("suggestedSource", "")
                if suggested_source and "source(" in suggested_source:
                    # Check if this replacement was made
                    full_ref = table_info.get("table", "")
                    if any(full_ref in r for r in replacements_made):
                        scheduled_project = table_info.get("scheduledQueryProject", "")
                        parts = full_ref.replace("`", "").replace('"', "").split(".")
                        if len(parts) >= 2:
                            table_display = f"{parts[-2]}.{parts[-1]}"
                        else:
                            table_display = parts[-1] if parts else full_ref

                        todo_item = {"project": scheduled_project, "table": table_display, "full_ref": full_ref}
                        todo_comments.append(todo_item)
                        logger.info(
                            f"[SQL Transform] Collected TODO for scheduled query dependency: {full_ref} from {scheduled_project}"
                        )

    elif not ast_success:
        # AST failed, fall back to regex
        logger.warning("[SQL Transform] ⚠️  AST transformation failed, falling back to regex")
        transformed = _transform_with_regex_fallback(
            transformed, external_tables, cross_project_decisions, todo_comments
        )

    # ============================================================================
    # PREPEND TODO/INFO COMMENTS - Add collected TODOs to top of SQL file
    # ============================================================================
    # Use centralized TodoCommentBuilder for consistent comment formatting
    auto_converted_count = len(original_declare_variables) - len(remaining_declare_variables)
    is_file_level = full_sql_declare_variables is not None

    comment_section = build_todo_comment(
        scheduled_query_todos=todo_comments,
        remaining_declare_variables=remaining_declare_variables,
        original_declare_variables=original_declare_variables if original_declare_variables else None,
        auto_converted_count=auto_converted_count,
        is_file_level_vars=is_file_level,
    )

    if comment_section:
        transformed = f"{comment_section}{transformed}"

        total_todos = len(todo_comments) + len(remaining_declare_variables)
        if total_todos > 0:
            logger.info(f"[SQL Transform] Added TODO list section with {total_todos} item(s) to top of SQL")
        elif original_declare_variables:
            if is_file_level:
                logger.info(
                    f"[SQL Transform] Added note about {len(original_declare_variables)} file-level DECLARE variable(s)"
                )
            else:
                logger.info(
                    f"[SQL Transform] Auto-converted {len(original_declare_variables)} DECLARE variable(s) to CTEs"
                )

    return transformed


def extract_and_transform_sql_for_table(
    sql: str,
    table_name: str,
    hardcoded_tables: list[dict],
    cross_project_decisions: dict[str, dict] | None = None,
    full_sql_declare_variables: list[dict] | None = None,
) -> str | None:
    """
    Extract SQL for a specific table AND transform it with source()/ref() calls.

    This combines extraction and transformation in one step for convenience.
    Also handles DECLARE/SET statements by converting them to inline CTEs.

    Args:
        sql: The full SQL content
        table_name: Name of the table to extract SQL for
        hardcoded_tables: List of table info dicts for transformation
        cross_project_decisions: Optional dict mapping "dataset.table" to decision dict
            for cross-project references
        full_sql_declare_variables: Optional list of DECLARE variables from the full SQL file.
            Used to check which variables are referenced in this specific table's SQL
            and add informational notes accordingly.

    Returns:
        Extracted and transformed SQL content, or None if extraction fails
    """
    # First, extract the raw SQL for this table from the ORIGINAL SQL
    # This must happen BEFORE any transformation to preserve statement boundaries
    extracted_sql = extract_sql_for_table(sql, table_name)

    if extracted_sql is None:
        return None

    # Check which DECLARE variables from the full SQL are referenced in this extracted SQL
    # Use word boundary matching to avoid false positives (e.g., 'date' matching 'update_date')
    variables_used_in_this_table = []
    if full_sql_declare_variables:
        for var in full_sql_declare_variables:
            var_name = var.get("variable", "")
            if var_name:
                # Use regex with word boundaries to match whole variable names only
                # This prevents matching 'date' inside 'update_date', etc.
                pattern = rf"\b{re.escape(var_name)}\b"
                if re.search(pattern, extracted_sql, re.IGNORECASE):
                    variables_used_in_this_table.append(var)

    # Transform with source() and ref() calls (including cross-project refs)
    # Pass the variables used in this specific table so notes are added appropriately
    transformed_sql = transform_sql_with_sources(
        extracted_sql,
        hardcoded_tables,
        cross_project_decisions,
        full_sql_declare_variables=variables_used_in_this_table,
    )

    return transformed_sql
