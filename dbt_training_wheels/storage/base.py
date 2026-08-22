"""Abstract storage layer interface.

Defines the contract for storage operations used across the application.
Implementations can provide filesystem, cloud storage, or mock backends.
"""

from abc import ABC, abstractmethod
from typing import Any


class StorageInterface(ABC):
    """Abstract base class for storage operations.

    Provides a consistent interface for:
    - Model configuration persistence
    - Temporary file management
    - File listing and existence checks
    """

    @abstractmethod
    def save_model_config(self, query_id: int, config: list[dict[str, Any]]) -> bool:
        """Save model configuration for a query.

        Args:
            query_id: The query identifier
            config: List of model configuration dictionaries

        Returns:
            True if save was successful

        Raises:
            FileSystemError: If save operation fails
        """
        pass

    @abstractmethod
    def load_model_config(self, query_id: int) -> list[dict[str, Any]] | None:
        """Load model configuration for a query.

        Args:
            query_id: The query identifier

        Returns:
            List of model configs if found, None otherwise
        """
        pass

    @abstractmethod
    def delete_model_config(self, query_id: int) -> bool:
        """Delete model configuration for a query.

        Args:
            query_id: The query identifier

        Returns:
            True if deletion was successful or file didn't exist
        """
        pass

    @abstractmethod
    def model_config_exists(self, query_id: int) -> bool:
        """Check if model configuration exists for a query.

        Args:
            query_id: The query identifier

        Returns:
            True if configuration exists
        """
        pass

    @abstractmethod
    def save_temp_file(self, filename: str, content: str) -> str:
        """Save content to a temporary file.

        Args:
            filename: Name for the temporary file
            content: Content to write

        Returns:
            Full path to the created file
        """
        pass

    @abstractmethod
    def read_temp_file(self, filename: str) -> str | None:
        """Read content from a temporary file.

        Args:
            filename: Name of the temporary file

        Returns:
            File content if found, None otherwise
        """
        pass

    @abstractmethod
    def get_temp_directory(self) -> str:
        """Get the temporary directory path.

        Returns:
            Absolute path to temp directory
        """
        pass

    @abstractmethod
    def list_files(self, directory: str, pattern: str = "*") -> list[str]:
        """List files matching a pattern in a directory.

        Args:
            directory: Directory path to search
            pattern: Glob pattern for matching (default: "*")

        Returns:
            List of matching file paths
        """
        pass

    @abstractmethod
    def file_exists(self, filepath: str) -> bool:
        """Check if a file exists.

        Args:
            filepath: Path to check

        Returns:
            True if file exists
        """
        pass

    @abstractmethod
    def ensure_directory(self, directory: str) -> None:
        """Ensure a directory exists, creating it if necessary.

        Args:
            directory: Directory path to ensure exists
        """
        pass
