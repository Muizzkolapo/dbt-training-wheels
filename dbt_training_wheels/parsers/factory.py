"""Factory for creating dialect-specific SQL parsers.

This module provides the factory function for obtaining the appropriate
SQL parser based on the database dialect.
"""

import logging

from dbt_training_wheels.parsers.base import SQLParserStrategy

logger = logging.getLogger(__name__)

# Registry of parser implementations
_PARSER_REGISTRY: dict[str, type[SQLParserStrategy]] = {}


def register_parser(dialect: str):
    """Decorator to register a parser implementation for a dialect."""

    def decorator(cls: type[SQLParserStrategy]):
        _PARSER_REGISTRY[dialect.lower()] = cls
        return cls

    return decorator


def get_parser(dialect: str = "bigquery", config: object | None = None) -> SQLParserStrategy:
    """
    Get the appropriate SQL parser for a database dialect.

    Args:
        dialect: Database dialect name (e.g., "bigquery", "postgres")
        config: Optional organization config for parser customization

    Returns:
        SQLParserStrategy implementation for the dialect

    Raises:
        ValueError: If dialect is not supported
    """
    dialect_lower = dialect.lower()

    # Try registered parsers first
    if dialect_lower in _PARSER_REGISTRY:
        return _PARSER_REGISTRY[dialect_lower](config)

    # Fall back to default parser (wraps existing sql_parser.py functions)
    from dbt_training_wheels.parsers.default import DefaultSQLParser

    logger.debug(f"Using default parser for dialect: {dialect}")
    return DefaultSQLParser(dialect, config)


def list_supported_dialects() -> list[str]:
    """Return list of dialects with registered parsers."""
    return list(_PARSER_REGISTRY.keys())
