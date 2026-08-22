"""Factory for creating configuration objects.

This module provides a factory pattern for creating OrganizationConfig instances
from various sources: YAML files, dialect presets, or custom dictionaries.

Usage:
    from dbt_training_wheels.factories import ConfigFactory

    # Create from dialect preset
    config = ConfigFactory.create_from_dialect("bigquery")

    # Create from YAML file
    config = ConfigFactory.create_from_yaml("/path/to/config.yaml")

    # Create from dictionary
    config = ConfigFactory.create_from_dict({"org_name": "MyOrg", ...})
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dbt_training_wheels.config_schema import (
    BIGQUERY_CONFIG,
    MYSQL_CONFIG,
    POSTGRES_CONFIG,
    SNOWFLAKE_CONFIG,
    OrganizationConfig,
    load_config_from_dict,
)

logger = logging.getLogger(__name__)


class ConfigFactory:
    """Factory for creating configuration objects.

    Provides multiple creation methods for different use cases:
    - create_from_dialect: Quick setup with preset configurations
    - create_from_yaml: Load from YAML configuration file
    - create_from_dict: Create from dictionary (useful for testing)
    - create_default: Get BigQuery preset as default
    """

    # Mapping of dialect names to preset configurations
    DIALECT_PRESETS: dict[str, OrganizationConfig] = {
        "bigquery": BIGQUERY_CONFIG,
        "postgres": POSTGRES_CONFIG,
        "postgresql": POSTGRES_CONFIG,  # Alias
        "snowflake": SNOWFLAKE_CONFIG,
        "mysql": MYSQL_CONFIG,
    }

    # Valid dialect names for validation
    VALID_DIALECTS = list(DIALECT_PRESETS.keys())

    @classmethod
    def create_from_dialect(cls, dialect: str) -> OrganizationConfig:
        """Create configuration from a dialect preset.

        Args:
            dialect: Database dialect name (bigquery, postgres, snowflake, mysql)

        Returns:
            OrganizationConfig with preset values for the dialect

        Raises:
            ValueError: If dialect is not recognized
        """
        dialect_lower = dialect.lower()
        if dialect_lower not in cls.DIALECT_PRESETS:
            valid = ", ".join(sorted(set(cls.DIALECT_PRESETS.keys())))
            raise ValueError(f"Unknown dialect: {dialect}. Valid options: {valid}")

        config = cls.DIALECT_PRESETS[dialect_lower]
        logger.debug(f"Created config from dialect preset: {dialect}")
        return config

    @classmethod
    def create_from_yaml(cls, yaml_path: str | Path) -> OrganizationConfig:
        """Create configuration from a YAML file.

        Args:
            yaml_path: Path to YAML configuration file

        Returns:
            OrganizationConfig loaded from the file

        Raises:
            FileNotFoundError: If the file doesn't exist
            ValueError: If the YAML is invalid or missing required fields
        """
        from dbt_training_wheels.config import load_organization_config

        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {yaml_path}")

        config = load_organization_config(str(path))
        logger.debug(f"Created config from YAML file: {yaml_path}")
        return config

    @classmethod
    def create_from_dict(cls, config_dict: dict[str, Any]) -> OrganizationConfig:
        """Create configuration from a dictionary.

        Useful for testing or programmatic configuration.

        Args:
            config_dict: Dictionary with configuration values

        Returns:
            OrganizationConfig instance

        Raises:
            ValueError: If the dictionary contains invalid values
        """
        config = load_config_from_dict(config_dict)
        logger.debug("Created config from dictionary")
        return config

    @classmethod
    def create_default(cls) -> OrganizationConfig:
        """Create default configuration (BigQuery preset).

        Returns:
            OrganizationConfig with BigQuery defaults
        """
        return cls.create_from_dialect("bigquery")

    @classmethod
    def create_for_testing(
        cls,
        org_name: str = "test_org",
        dialect: str = "bigquery",
        **overrides: Any,
    ) -> OrganizationConfig:
        """Create a configuration suitable for testing.

        Provides sensible defaults with ability to override specific values.

        Args:
            org_name: Organization name
            dialect: Database dialect
            **overrides: Additional fields to override

        Returns:
            OrganizationConfig configured for testing
        """
        # Build config dict with overrides
        config_dict = {
            "org_name": org_name,
            "database": {"dialect": dialect},
        }
        config_dict.update(overrides)

        # Merge with base config values
        return load_config_from_dict(config_dict)

    @classmethod
    def get_available_dialects(cls) -> list[str]:
        """Get list of available dialect presets.

        Returns:
            List of valid dialect names
        """
        # Return unique values (removing aliases like postgresql)
        return sorted({"bigquery", "postgres", "snowflake", "mysql"})

    @classmethod
    def is_valid_dialect(cls, dialect: str) -> bool:
        """Check if a dialect name is valid.

        Args:
            dialect: Dialect name to check

        Returns:
            True if dialect is recognized, False otherwise
        """
        return dialect.lower() in cls.DIALECT_PRESETS
