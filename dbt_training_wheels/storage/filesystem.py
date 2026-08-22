"""Filesystem implementation of storage interface.

Provides local filesystem storage for model configurations and temporary files.
"""

import glob
import json
import logging
import os
from pathlib import Path
from typing import Any

from dbt_training_wheels.exceptions import FileSystemError
from dbt_training_wheels.storage.base import StorageInterface

logger = logging.getLogger(__name__)


class FileSystemStorage(StorageInterface):
    """Local filesystem storage implementation.

    Stores model configurations as JSON files in a temp directory.
    Supports customizable base path for testing.

    Attributes:
        base_path: Root directory for all storage operations
        temp_dir: Directory for temporary files
    """

    def __init__(self, base_path: str | None = None):
        """Initialize filesystem storage.

        Args:
            base_path: Optional custom base path. If not provided,
                      defaults to project root directory.
        """
        if base_path:
            self.base_path = Path(base_path)
        else:
            # Default: project root (3 levels up from this file)
            self.base_path = Path(__file__).parent.parent.parent

        self.temp_dir = self.base_path / "temp"
        self._ensure_temp_dir()
        logger.debug(f"FileSystemStorage initialized with base: {self.base_path}")

    def _ensure_temp_dir(self) -> None:
        """Ensure temp directory exists."""
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _get_model_config_path(self, query_id: int) -> Path:
        """Get path for model config file.

        Args:
            query_id: The query identifier

        Returns:
            Path to model config JSON file
        """
        return self.temp_dir / f"model_config_{query_id}.json"

    def save_model_config(self, query_id: int, config: list[dict[str, Any]]) -> bool:
        """Save model configuration to JSON file.

        Args:
            query_id: The query identifier
            config: List of model configuration dictionaries

        Returns:
            True if save was successful

        Raises:
            FileSystemError: If write operation fails
        """
        config_path = self._get_model_config_path(query_id)

        try:
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            logger.debug(f"Saved model config for query {query_id} to {config_path}")
            return True
        except PermissionError as err:
            raise FileSystemError.permission_denied(str(config_path)) from err
        except OSError as err:
            logger.error(f"Failed to save model config: {err}")
            raise FileSystemError(
                user_message=f"Failed to save configuration for query {query_id}",
                beginner_help="The system couldn't write the configuration file",
                common_fixes=["Check disk space", "Verify write permissions"],
                docs_anchor="filesystem-errors",
                technical_message=str(err),
            ) from err

    def load_model_config(self, query_id: int) -> list[dict[str, Any]] | None:
        """Load model configuration from JSON file.

        Args:
            query_id: The query identifier

        Returns:
            List of model configs if found, None otherwise
        """
        config_path = self._get_model_config_path(query_id)

        if not config_path.exists():
            logger.debug(f"No model config found for query {query_id}")
            return None

        try:
            with open(config_path) as f:
                config: list[dict[str, Any]] = json.load(f)
            logger.debug(f"Loaded model config for query {query_id}")
            return config
        except json.JSONDecodeError as err:
            logger.warning(f"Invalid JSON in model config {config_path}: {err}")
            return None
        except PermissionError as err:
            raise FileSystemError.permission_denied(str(config_path)) from err
        except OSError as err:
            logger.warning(f"Could not load model config from {config_path}: {err}")
            return None

    def delete_model_config(self, query_id: int) -> bool:
        """Delete model configuration file.

        Args:
            query_id: The query identifier

        Returns:
            True if deletion was successful or file didn't exist
        """
        config_path = self._get_model_config_path(query_id)

        if not config_path.exists():
            return True

        try:
            config_path.unlink()
            logger.debug(f"Deleted model config for query {query_id}")
            return True
        except OSError as err:
            logger.error(f"Failed to delete model config: {err}")
            return False

    def model_config_exists(self, query_id: int) -> bool:
        """Check if model configuration exists.

        Args:
            query_id: The query identifier

        Returns:
            True if configuration file exists
        """
        return self._get_model_config_path(query_id).exists()

    def save_temp_file(self, filename: str, content: str) -> str:
        """Save content to a temporary file.

        Args:
            filename: Name for the temporary file
            content: Content to write

        Returns:
            Full path to the created file

        Raises:
            FileSystemError: If write operation fails
        """
        filepath = self.temp_dir / filename

        try:
            with open(filepath, "w") as f:
                f.write(content)
            logger.debug(f"Saved temp file: {filepath}")
            return str(filepath)
        except PermissionError as err:
            raise FileSystemError.permission_denied(str(filepath)) from err
        except OSError as err:
            raise FileSystemError(
                user_message=f"Failed to save temporary file: {filename}",
                beginner_help="The system couldn't write the temporary file",
                common_fixes=["Check disk space", "Verify write permissions"],
                docs_anchor="filesystem-errors",
                technical_message=str(err),
            ) from err

    def read_temp_file(self, filename: str) -> str | None:
        """Read content from a temporary file.

        Args:
            filename: Name of the temporary file

        Returns:
            File content if found, None otherwise
        """
        filepath = self.temp_dir / filename

        if not filepath.exists():
            return None

        try:
            with open(filepath) as f:
                return f.read()
        except OSError as err:
            logger.warning(f"Could not read temp file {filepath}: {err}")
            return None

    def get_temp_directory(self) -> str:
        """Get the temporary directory path.

        Returns:
            Absolute path to temp directory
        """
        return str(self.temp_dir.absolute())

    def list_files(self, directory: str, pattern: str = "*") -> list[str]:
        """List files matching a pattern in a directory.

        Args:
            directory: Directory path to search
            pattern: Glob pattern for matching (default: "*")

        Returns:
            List of matching file paths
        """
        search_path = os.path.join(directory, pattern)
        return glob.glob(search_path)

    def file_exists(self, filepath: str) -> bool:
        """Check if a file exists.

        Args:
            filepath: Path to check

        Returns:
            True if file exists
        """
        return os.path.exists(filepath)

    def ensure_directory(self, directory: str) -> None:
        """Ensure a directory exists, creating it if necessary.

        Args:
            directory: Directory path to ensure exists
        """
        os.makedirs(directory, exist_ok=True)


# Module-level singleton for convenience
_default_storage: FileSystemStorage | None = None


def get_storage() -> FileSystemStorage:
    """Get the default filesystem storage instance.

    Returns:
        FileSystemStorage singleton instance
    """
    global _default_storage
    if _default_storage is None:
        _default_storage = FileSystemStorage()
    return _default_storage


def reset_storage() -> None:
    """Reset the default storage instance.

    Useful for testing.
    """
    global _default_storage
    _default_storage = None
