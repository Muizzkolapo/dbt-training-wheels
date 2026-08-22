"""Centralized naming resolution for dbt_training_wheels models.

This service provides a single source of truth for resolving naming configurations
across the application, eliminating code duplication and ensuring consistency.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import OrganizationConfig
    from dbt_training_wheels.models.query_configuration import NamingConfiguration


@dataclass
class ResolvedNaming:
    """Resolved naming configuration with all computed values.

    This represents the final, resolved naming configuration after applying
    precedence rules: query-specific overrides > project config > org defaults.

    layer_folder_names is the single source of truth for folder names.
    Individual folder properties (staging_folder, etc.) are computed from this dict.
    """

    # 2-layer architecture prefixes
    staging_model_prefix: str
    intermediate_model_prefix: str
    mart_model_prefix: str

    # Single source of truth for folder names
    layer_folder_names: dict[str, str]

    # Style settings
    case_style: str
    separator: str
    source_name_from: str

    # Folder structure
    use_layer_folders: bool

    # Property getters for backward compatibility
    @property
    def staging_folder(self) -> str:
        """Get staging folder name from layer_folder_names dict."""
        return self.layer_folder_names.get("staging", "staging")

    @property
    def intermediate_folder(self) -> str:
        """Get intermediate folder name from layer_folder_names dict."""
        return self.layer_folder_names.get("intermediate", "intermediate")

    @property
    def marts_folder(self) -> str:
        """Get marts folder name from layer_folder_names dict."""
        return self.layer_folder_names.get("marts", "marts")


class NamingResolver:
    """Resolves naming configuration from multiple precedence levels.

    Precedence order (highest to lowest):
    1. QueryConfiguration naming (user-specific overrides)
    2. Project-specific config
    3. Defaults from OrganizationConfig

    This eliminates the 60+ lines of duplicated naming logic that previously
    existed in analysis_service.py, file_generator.py, and query_config_service.py.
    """

    def __init__(self, org_config: "OrganizationConfig"):
        """Initialize resolver with organization config.

        Args:
            org_config: The organization configuration containing defaults
        """
        self.org_config = org_config

    def resolve(
        self,
        query_naming: Optional["NamingConfiguration"] = None,
        project_name: str | None = None,
    ) -> ResolvedNaming:
        """Resolve naming configuration with proper precedence.

        Args:
            query_naming: Query-specific naming overrides from QueryConfiguration
            project_name: Project name for project-specific config lookup

        Returns:
            Fully resolved naming configuration with all fields populated
        """
        # Get project-specific config if available
        project_config = None
        if project_name and self.org_config.projects and project_name in self.org_config.projects:
            project_data = self.org_config.projects[project_name]
            if project_data and project_data.dbt_config and project_data.dbt_config.naming:
                project_config = project_data.dbt_config.naming

        # Get org defaults
        defaults = None
        if self.org_config.defaults and self.org_config.defaults.dbt_config:
            defaults = self.org_config.defaults.dbt_config.naming

        # Resolve each field with precedence logic
        # Build layer_folder_names dict as single source of truth
        layer_folder_names = {
            "staging": self._resolve_layer_folder(query_naming, project_config, "staging", defaults),
            "intermediate": self._resolve_layer_folder(query_naming, project_config, "intermediate", defaults),
            "marts": self._resolve_layer_folder(query_naming, project_config, "marts", defaults),
        }

        return ResolvedNaming(
            staging_model_prefix=self._resolve_field(
                query_naming,
                "staging_model_prefix",
                project_config,
                "staging_model_prefix",
                getattr(defaults, "staging_model_prefix", "stg__") if defaults else "stg__",
            ),
            intermediate_model_prefix=self._resolve_field(
                query_naming,
                "intermediate_model_prefix",
                project_config,
                "intermediate_model_prefix",
                getattr(defaults, "intermediate_model_prefix", "int__") if defaults else "int__",
            ),
            mart_model_prefix=self._resolve_field(
                query_naming,
                "mart_model_prefix",
                project_config,
                "mart_model_prefix",
                getattr(defaults, "mart_model_prefix", "") if defaults else "",
            ),
            layer_folder_names=layer_folder_names,
            case_style=self._resolve_field(
                query_naming,
                "case_style",
                project_config,
                "case_style",
                getattr(defaults, "case_style", "snake_case") if defaults else "snake_case",
            ),
            separator=self._resolve_field(
                query_naming,
                "separator",
                project_config,
                "separator",
                getattr(defaults, "separator", "__") if defaults else "__",
            ),
            source_name_from=self._resolve_field(
                query_naming,
                "source_name_from",
                project_config,
                "source_name_from",
                getattr(defaults, "source_name_from", "table") if defaults else "table",
            ),
            use_layer_folders=self._resolve_field(
                query_naming,
                "use_layer_folders",
                project_config,
                "use_layer_folders",
                getattr(defaults, "use_layer_folders", True) if defaults else True,
            ),
        )

    def _resolve_field(
        self,
        query_naming: Optional["NamingConfiguration"],
        query_field: str,
        project_config: object | None,
        project_field: str,
        default_value: Any,
    ) -> Any:
        """Resolve a single field with precedence logic.

        Args:
            query_naming: Query-specific naming configuration
            query_field: Field name in query_naming
            project_config: Project-specific naming configuration
            project_field: Field name in project_config
            default_value: Fallback default value

        Returns:
            Resolved field value
        """
        # Check query-specific override (highest priority)
        if query_naming:
            value = getattr(query_naming, query_field, None)
            if value is not None and value != "":
                return value

        # Check project-specific config (medium priority)
        if project_config:
            value = getattr(project_config, project_field, None)
            if value is not None and value != "":
                return value

        # Fall back to default (lowest priority)
        return default_value

    def _resolve_layer_folder(
        self,
        query_naming: Optional["NamingConfiguration"],
        project_config: object | None,
        layer: str,
        defaults: object | None,
    ) -> str:
        """Resolve folder name for a specific layer.

        Args:
            query_naming: Query-specific naming configuration
            project_config: Project-specific naming configuration
            layer: Layer name ("intermediate" or "marts")
            defaults: Default naming configuration

        Returns:
            Resolved folder name for the layer
        """
        # Check query-specific layer_folder_names (highest priority)
        if query_naming and query_naming.layer_folder_names:
            folder = query_naming.layer_folder_names.get(layer)
            if folder:
                return folder

        # Check project-specific config (medium priority)
        if project_config and hasattr(project_config, "layer_folder_names"):
            layer_folders = getattr(project_config, "layer_folder_names", None)
            if layer_folders and layer in layer_folders:
                return str(layer_folders[layer])

        # Fall back to org defaults (lowest priority)
        if defaults and hasattr(defaults, "layer_folder_names"):
            layer_folders = getattr(defaults, "layer_folder_names", None)
            if layer_folders and layer in layer_folders:
                return str(layer_folders[layer])

        # Ultimate fallback: use layer name as-is
        return layer
