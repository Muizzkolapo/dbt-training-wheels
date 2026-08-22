"""Base Repository interfaces for data access.

This module defines abstract repository interfaces following the Repository pattern.
Concrete implementations can use in-memory storage, file system, or database backends.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Query:
    """Represents a SQL query with metadata."""

    id: int
    name: str
    sql: str
    tables: list[str]
    file_name: str | None = None
    analysis_data: dict | None = None


@dataclass
class ModelConfig:
    """Represents configuration for a dbt model."""

    query_id: int
    table: str
    materialization: str = "table"
    schema: str | None = None
    tags: list[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class Repository(ABC, Generic[T]):
    """Generic repository interface."""

    @abstractmethod
    def get(self, id: int) -> T | None:
        """Get entity by ID."""
        pass

    @abstractmethod
    def get_all(self) -> list[T]:
        """Get all entities."""
        pass

    @abstractmethod
    def save(self, entity: T) -> T:
        """Save entity (create or update)."""
        pass

    @abstractmethod
    def delete(self, id: int) -> bool:
        """Delete entity by ID. Returns True if deleted."""
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear all entities."""
        pass


class QueryRepository(Repository[Query]):
    """Repository interface for Query entities."""

    @abstractmethod
    def get_by_name(self, name: str) -> Query | None:
        """Get query by name."""
        pass

    @abstractmethod
    def get_next_id(self) -> int:
        """Get the next available query ID."""
        pass


class ModelConfigRepository(Repository[ModelConfig]):
    """Repository interface for ModelConfig entities."""

    @abstractmethod
    def get_by_query_id(self, query_id: int) -> list[ModelConfig]:
        """Get all model configs for a query."""
        pass

    @abstractmethod
    def save_for_query(self, query_id: int, configs: list[ModelConfig]) -> list[ModelConfig]:
        """Save all model configs for a query (replaces existing)."""
        pass

    @abstractmethod
    def delete_by_query_id(self, query_id: int) -> int:
        """Delete all configs for a query. Returns count deleted."""
        pass
