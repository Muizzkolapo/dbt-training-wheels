"""Service container for dependency injection.

This module provides a centralized container for managing service dependencies
across the application. It supports lazy initialization and easy testing via reset.

Usage:
    from dbt_training_wheels.container import get_container

    # In route handlers
    container = get_container()
    config = container.config
    github = container.github_service

    # In tests
    from dbt_training_wheels.container import ServiceContainer
    ServiceContainer.reset()  # Reset singleton
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import GitHubConfig, OrganizationConfig
    from dbt_training_wheels.services.github_service import GitHubService
    from dbt_training_wheels.services.template_service import TemplateService

logger = logging.getLogger(__name__)


class ServiceContainer:
    """Centralized dependency container for services.

    Implements a singleton pattern with lazy service initialization.
    Services are instantiated on first access and cached for subsequent use.

    Attributes:
        config: Organization configuration instance
    """

    _instance: ServiceContainer | None = None

    def __init__(self, config: OrganizationConfig | None = None):
        """Initialize the service container.

        Args:
            config: Optional organization config. If not provided,
                   will be loaded via get_org_config().
        """
        from dbt_training_wheels.config import get_org_config

        self._config = config or get_org_config()
        self._services: dict = {}
        logger.debug("ServiceContainer initialized")

    @classmethod
    def get_instance(cls, config: OrganizationConfig | None = None) -> ServiceContainer:
        """Get the singleton container instance.

        Args:
            config: Optional config to use when creating new instance.
                   Ignored if instance already exists.

        Returns:
            ServiceContainer singleton instance
        """
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the container singleton.

        Useful for testing to ensure fresh state between tests.
        """
        cls._instance = None
        logger.debug("ServiceContainer reset")

    @classmethod
    def configure(cls, config: OrganizationConfig) -> ServiceContainer:
        """Configure and return container with specific config.

        Resets any existing instance and creates new one with provided config.

        Args:
            config: Organization configuration to use

        Returns:
            New ServiceContainer instance
        """
        cls.reset()
        cls._instance = cls(config)
        return cls._instance

    @property
    def config(self) -> OrganizationConfig:
        """Get the organization configuration."""
        return self._config

    @property
    def github_service(self) -> GitHubService:
        """Get or create the GitHub service.

        Returns:
            GitHubService instance configured with current GitHub config
        """
        if "github" not in self._services:
            from dbt_training_wheels.services.github_service import GitHubService

            if self._config and self._config.github:
                self._services["github"] = GitHubService(self._config.github)
            else:
                # Create with empty config - service will handle gracefully
                from dbt_training_wheels.config_schema import GitHubConfig

                self._services["github"] = GitHubService(GitHubConfig())
        return self._services["github"]

    @property
    def template_service(self) -> TemplateService:
        """Get or create the template service.

        Returns:
            TemplateService instance configured with current config
        """
        if "template" not in self._services:
            from dbt_training_wheels.services.template_service import TemplateService

            self._services["template"] = TemplateService(self._config)
        return self._services["template"]

    def create_github_service(self, github_config: GitHubConfig) -> GitHubService:
        """Create a GitHub service with custom config.

        Unlike the github_service property which uses the container's config,
        this method creates a new service with the provided config.

        Args:
            github_config: GitHub configuration to use

        Returns:
            New GitHubService instance
        """
        from dbt_training_wheels.services.github_service import GitHubService

        return GitHubService(github_config)

    def clear_service(self, service_name: str) -> None:
        """Clear a cached service, forcing re-creation on next access.

        Args:
            service_name: Name of service to clear (github, template)
        """
        if service_name in self._services:
            del self._services[service_name]
            logger.debug(f"Cleared cached service: {service_name}")


def get_container() -> ServiceContainer:
    """Get the service container singleton.

    Convenience function for accessing the container.

    Returns:
        ServiceContainer singleton instance
    """
    return ServiceContainer.get_instance()
