"""Base SQL Parser Strategy - defines interface for dialect-specific parsers.

This module implements the Strategy pattern for SQL parsing, allowing different
database dialects to have their own parsing implementations while maintaining
a consistent interface.

Usage:
    parser = get_parser("bigquery")
    tables = parser.extract_tables(sql)
    transformed = parser.transform_to_source(sql, tables)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ParsedTable:
    """Represents a parsed table reference."""

    full_name: str  # Full qualified name (e.g., "project.dataset.table")
    project: str | None = None  # Database/project
    schema: str | None = None  # Schema/dataset
    table: str = ""  # Table name
    alias: str | None = None  # Table alias if present
    is_self_reference: bool = False  # True if table is created in the same script


@dataclass
class ParsedSQL:
    """Result of SQL parsing."""

    original_sql: str
    sql_type: str  # "create_table", "insert_into", "standalone_select", etc.
    tables: list[ParsedTable]
    created_tables: list[str]  # Tables created by the script
    ctes: list[str]  # CTE names


class SQLParserStrategy(ABC):
    """Abstract base class for SQL parser implementations.

    Each database dialect should implement this interface to provide
    dialect-specific parsing behavior.
    """

    @property
    @abstractmethod
    def dialect(self) -> str:
        """Return the dialect name (e.g., 'bigquery', 'postgres')."""
        pass

    @property
    @abstractmethod
    def quote_char(self) -> str:
        """Return the quote character for identifiers (e.g., '`', '"')."""
        pass

    @abstractmethod
    def extract_tables(self, sql: str) -> list[ParsedTable]:
        """
        Extract all table references from SQL.

        Args:
            sql: SQL query string

        Returns:
            List of ParsedTable objects
        """
        pass

    @abstractmethod
    def extract_created_tables(self, sql: str) -> list[str]:
        """
        Extract tables created by CREATE TABLE or INSERT INTO statements.

        Args:
            sql: SQL query string

        Returns:
            List of table names created by the script
        """
        pass

    @abstractmethod
    def detect_sql_type(self, sql: str) -> str:
        """
        Detect the type of SQL statement.

        Args:
            sql: SQL query string

        Returns:
            One of: "create_table", "insert_into", "standalone_select",
                   "with_cte", "merge", "unknown"
        """
        pass

    @abstractmethod
    def transform_to_source(self, sql: str, tables: list[ParsedTable], source_mapping: dict | None = None) -> str:
        """
        Transform hardcoded table references to dbt source() calls.

        Args:
            sql: Original SQL string
            tables: List of parsed tables to transform
            source_mapping: Optional mapping of table names to source names (for future use)

        Returns:
            Transformed SQL with source() calls
        """
        pass

    def parse(self, sql: str) -> ParsedSQL:
        """
        Full parse of SQL returning all extracted information.

        Args:
            sql: SQL query string

        Returns:
            ParsedSQL object with all parsing results
        """
        return ParsedSQL(
            original_sql=sql,
            sql_type=self.detect_sql_type(sql),
            tables=self.extract_tables(sql),
            created_tables=self.extract_created_tables(sql),
            ctes=self._extract_ctes(sql),
        )

    def _extract_ctes(self, sql: str) -> list[str]:
        """Extract CTE names from SQL. Default implementation."""
        import re

        cte_pattern = r"WITH\s+(\w+)\s+AS\s*\("
        return re.findall(cte_pattern, sql, re.IGNORECASE)
