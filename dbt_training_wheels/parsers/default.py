"""Default SQL Parser - wraps existing sql_parser.py for backward compatibility.

This parser provides a Strategy interface over the existing parsing functions,
allowing gradual conversion to dialect-specific implementations.
"""

from dbt_training_wheels.parsers.base import ParsedTable, SQLParserStrategy


class DefaultSQLParser(SQLParserStrategy):
    """Default parser that wraps existing sql_parser.py functions."""

    def __init__(self, dialect: str = "bigquery", config: object | None = None):
        self._dialect = dialect
        self._config = config

    @property
    def dialect(self) -> str:
        return self._dialect

    @property
    def quote_char(self) -> str:
        quote_map = {
            "bigquery": "`",
            "postgres": '"',
            "postgresql": '"',
            "mysql": "`",
            "snowflake": '"',
            "redshift": '"',
            "oracle": '"',
        }
        return quote_map.get(self._dialect.lower(), "`")

    def extract_tables(self, sql: str) -> list[ParsedTable]:
        """Extract tables using existing parser."""
        from dbt_training_wheels.utils.sql_parser import extract_table_references

        raw_tables = extract_table_references(sql, self._config)
        tables = []

        for table_info in raw_tables:
            full_name = table_info.get("table", "")
            parts = full_name.split(".")

            if len(parts) >= 3:
                project, schema, table = parts[-3], parts[-2], parts[-1]
            elif len(parts) == 2:
                project, schema, table = None, parts[-2], parts[-1]
            else:
                project, schema, table = None, None, parts[-1] if parts else ""

            tables.append(
                ParsedTable(
                    full_name=full_name,
                    project=project,
                    schema=schema,
                    table=table,
                    is_self_reference=table_info.get("isSelfReference", False),
                )
            )

        return tables

    def extract_created_tables(self, sql: str) -> list[str]:
        """Extract created tables using existing parser."""
        from dbt_training_wheels.utils.sql_parser import extract_created_tables

        return extract_created_tables(sql)

    def detect_sql_type(self, sql: str) -> str:
        """Detect SQL type using existing parser."""
        from dbt_training_wheels.utils.sql_parser import detect_sql_type

        return detect_sql_type(sql)

    def transform_to_source(self, sql: str, tables: list[ParsedTable], source_mapping: dict | None = None) -> str:
        """Transform SQL using existing parser."""
        from dbt_training_wheels.utils.sql_parser import transform_sql_with_sources

        # Convert ParsedTable to dict format expected by existing function
        hardcoded_tables = [
            {
                "table": t.full_name,
                "isSelfReference": t.is_self_reference,
            }
            for t in tables
            if not t.is_self_reference
        ]

        # source_mapping reserved for future custom source name mappings
        _ = source_mapping

        return transform_sql_with_sources(sql, hardcoded_tables)
