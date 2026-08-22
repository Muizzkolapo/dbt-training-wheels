"""Repository pattern implementations for data access.

This module provides abstract repository interfaces and implementations
for accessing queries, model configurations, and other data.
"""

from dbt_training_wheels.repositories.base import ModelConfigRepository, QueryRepository
from dbt_training_wheels.repositories.memory import InMemoryModelConfigRepository, InMemoryQueryRepository

__all__ = [
    "QueryRepository",
    "ModelConfigRepository",
    "InMemoryQueryRepository",
    "InMemoryModelConfigRepository",
]
