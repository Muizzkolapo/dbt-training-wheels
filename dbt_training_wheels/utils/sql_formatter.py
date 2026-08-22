"""SQL formatting utilities for consistent, readable code style."""

import re

try:
    import sqlglot

    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False


def format_sql(sql: str, dialect: str = "bigquery") -> str:
    """
    Format SQL for consistent, readable output.

    Uses sqlglot's pretty printing for proper AST-aware formatting,
    with a fallback to basic regex-based formatting.

    Args:
        sql: The SQL content to format
        dialect: SQL dialect (bigquery, postgres, snowflake, etc.)

    Returns:
        Formatted SQL string
    """
    if not sql or not sql.strip():
        return sql

    # First, try sqlglot's pretty printing
    if SQLGLOT_AVAILABLE:
        try:
            formatted = _format_with_sqlglot(sql, dialect)
            if formatted:
                return formatted
        except Exception:
            pass  # Fall back to basic formatting

    # Fallback: basic formatting
    return _basic_format(sql)


def _format_with_sqlglot(sql: str, dialect: str = "bigquery") -> str | None:
    """
    Format SQL using sqlglot's pretty printing.

    This handles Jinja templates by temporarily replacing them.
    """
    # Extract Jinja blocks to preserve them
    jinja_blocks: list[str] = []
    jinja_pattern = r"(\{\{.*?\}\}|\{%.*?%\})"

    def replace_jinja(match):
        placeholder = f"__JINJA_PLACEHOLDER_{len(jinja_blocks)}__"
        jinja_blocks.append(match.group(0))
        return placeholder

    # Replace Jinja with placeholders
    sql_without_jinja = re.sub(jinja_pattern, replace_jinja, sql, flags=re.DOTALL)

    try:
        # Parse and pretty print
        parsed = sqlglot.parse_one(sql_without_jinja, dialect=dialect)
        formatted = str(parsed.sql(dialect=dialect, pretty=True))

        # Restore Jinja blocks
        for i, block in enumerate(jinja_blocks):
            formatted = formatted.replace(f"__JINJA_PLACEHOLDER_{i}__", block)

        return formatted
    except Exception:
        return None


def _basic_format(sql: str) -> str:
    """
    Basic SQL formatting without external dependencies.

    This provides minimal formatting when sqlglot isn't available
    or fails to parse the SQL.
    """
    if not sql:
        return sql

    # Keywords that should start on a new line
    keywords_newline_before = [
        "SELECT",
        "FROM",
        "WHERE",
        "AND",
        "OR",
        "GROUP BY",
        "ORDER BY",
        "HAVING",
        "LIMIT",
        "OFFSET",
        "UNION",
        "INTERSECT",
        "EXCEPT",
        "LEFT JOIN",
        "RIGHT JOIN",
        "INNER JOIN",
        "OUTER JOIN",
        "CROSS JOIN",
        "LEFT OUTER JOIN",
        "RIGHT OUTER JOIN",
        "FULL OUTER JOIN",
        "JOIN",
        "ON",
        "USING",
        "WITH",
        "AS (",
        "CASE",
        "WHEN",
        "THEN",
        "ELSE",
        "END",
        "PARTITION BY",
        "QUALIFY",
    ]

    result = sql

    # Add newlines before major keywords
    for keyword in keywords_newline_before:
        # Case-insensitive replacement, preserving original case
        pattern = rf"(?<!\w)({re.escape(keyword)})(?!\w)"
        result = re.sub(pattern, r"\n\1", result, flags=re.IGNORECASE)

    # Handle CTE definitions: add newline after AS (
    result = re.sub(r"\bAS\s*\(\s*SELECT", r"AS (\n  SELECT", result, flags=re.IGNORECASE)

    # Handle closing parentheses for CTEs
    result = re.sub(r"\)\s*,\s*(\w+)\s+AS\s*\(", r"),\n\n\1 AS (", result, flags=re.IGNORECASE)

    # Clean up multiple newlines
    result = re.sub(r"\n{3,}", "\n\n", result)

    # Clean up spaces before newlines
    result = re.sub(r" +\n", "\n", result)

    # Clean up leading/trailing whitespace on each line
    lines = result.split("\n")
    lines = [line.strip() for line in lines]

    # Basic indentation for SQL blocks
    formatted_lines = []
    indent_level = 0

    for line in lines:
        if not line:
            formatted_lines.append("")
            continue

        upper_line = line.upper()

        # Decrease indent for closing elements
        if upper_line.startswith(")") or upper_line.startswith("END"):
            indent_level = max(0, indent_level - 1)

        # Add the line with current indentation
        formatted_lines.append("  " * indent_level + line)

        # Increase indent for opening elements
        if upper_line.endswith("(") or upper_line.startswith("CASE"):
            indent_level += 1

    return "\n".join(formatted_lines).strip()


def format_dbt_model(content: str, dialect: str = "bigquery") -> str:
    """
    Format a dbt model file, preserving Jinja blocks and comments.

    Args:
        content: Full dbt model content (may include config block and comments)
        dialect: SQL dialect for formatting

    Returns:
        Formatted model content
    """
    if not content:
        return content

    # Split into header (config, comments) and SQL
    lines = content.split("\n")
    header_lines = []
    sql_lines = []
    in_header = True

    for line in lines:
        stripped = line.strip()
        # Header includes: config blocks {{ }}, comments --, empty lines at start
        if in_header:
            if stripped.startswith("{{") or stripped.startswith("--") or not stripped:
                header_lines.append(line)
            else:
                in_header = False
                sql_lines.append(line)
        else:
            sql_lines.append(line)

    # Format only the SQL portion
    sql_content = "\n".join(sql_lines)
    formatted_sql = format_sql(sql_content, dialect)

    # Reconstruct with header
    if header_lines:
        header = "\n".join(header_lines)
        # Ensure proper spacing between header and SQL
        if not header.endswith("\n\n"):
            header = header.rstrip("\n") + "\n\n"
        return header + formatted_sql
    else:
        return formatted_sql
