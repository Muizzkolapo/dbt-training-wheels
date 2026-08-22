"""Template service for rendering dbt model templates."""

import os
from typing import TYPE_CHECKING, Optional

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import OrganizationConfig

# Default template directory (inside package)
DEFAULT_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "dbt")


class TemplateService:
    """Service for loading and rendering dbt templates."""

    def __init__(self, config: Optional["OrganizationConfig"] = None):
        """
        Initialize template service.

        Args:
            config: Organization config with optional custom template path
        """
        self.config = config
        self._env: Environment | None = None
        self._custom_template_path: str | None = None

        # Check for custom template path in config
        if config and hasattr(config, "templates") and config.templates:
            self._custom_template_path = getattr(config.templates, "dbt_template_path", None)

    @property
    def env(self) -> Environment:
        """Get or create Jinja2 environment."""
        if self._env is None:
            # Build list of template directories (custom first, then default)
            template_dirs = []

            if self._custom_template_path and os.path.isdir(self._custom_template_path):
                template_dirs.append(self._custom_template_path)

            if os.path.isdir(DEFAULT_TEMPLATE_DIR):
                template_dirs.append(DEFAULT_TEMPLATE_DIR)

            if template_dirs:
                self._env = Environment(
                    loader=FileSystemLoader(template_dirs),
                    trim_blocks=True,
                    lstrip_blocks=True,
                    keep_trailing_newline=True,
                )
            else:
                # No template directories available - templates will fail gracefully
                self._env = Environment()

        return self._env

    def template_exists(self, template_name: str) -> bool:
        """
        Check if a template file exists.

        Args:
            template_name: Name of template file (e.g., 'final_model.sql.j2')

        Returns:
            True if template exists, False otherwise
        """
        try:
            self.env.get_template(template_name)
            return True
        except TemplateNotFound:
            return False

    def render_template(self, template_name: str, **context) -> str | None:
        """
        Render a template with the given context.

        Args:
            template_name: Name of template file (e.g., 'final_model.sql.j2')
            **context: Variables to pass to template

        Returns:
            Rendered template string, or None if template not found
        """
        try:
            template = self.env.get_template(template_name)
            return str(template.render(**context))
        except TemplateNotFound:
            return None


# Singleton instance
_template_service: TemplateService | None = None


def get_template_service(
    config: Optional["OrganizationConfig"] = None,
) -> TemplateService:
    """
    Get or create template service singleton.

    Args:
        config: Optional organization config. If provided, recreates the service.

    Returns:
        TemplateService instance
    """
    global _template_service
    if _template_service is None or config is not None:
        _template_service = TemplateService(config)
    return _template_service


def reset_template_service():
    """Reset the template service singleton (useful for testing)."""
    global _template_service
    _template_service = None
