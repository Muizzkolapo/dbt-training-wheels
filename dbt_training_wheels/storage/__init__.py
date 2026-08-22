"""Storage abstraction layer for file operations."""

from dbt_training_wheels.storage.base import StorageInterface
from dbt_training_wheels.storage.filesystem import FileSystemStorage

__all__ = ["StorageInterface", "FileSystemStorage"]
