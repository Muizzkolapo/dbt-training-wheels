"""
Service for managing QueryConfiguration state.

This service provides centralized state management for the conversion wizard,
ensuring naming prefixes are computed ONCE and all decisions are persisted
to a single configuration file.
"""

import json
import logging
from typing import TYPE_CHECKING, Optional

from dbt_training_wheels.models.query_configuration import (
    CrossProjectDecision,
    ModelConfiguration,
    NamingConfiguration,
    QueryConfiguration,
)
from dbt_training_wheels.models.types import AnalysisResult
from dbt_training_wheels.services.naming_resolver import NamingResolver
from dbt_training_wheels.storage import FileSystemStorage

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import OrganizationConfig

logger = logging.getLogger(__name__)


class QueryConfigService:
    """
    Centralized service for query configuration management.

    Responsibilities:
    - Create/load/save QueryConfiguration
    - Compute naming prefixes (once per query)
    - Provide typed access to configuration sections
    - Handle conversion from old format
    """

    def __init__(
        self,
        storage: FileSystemStorage | None = None,
        config: Optional["OrganizationConfig"] = None,
    ):
        """
        Initialize the service.

        Args:
            storage: FileSystemStorage instance (uses default if None)
            config: OrganizationConfig instance
        """
        self.storage = storage or FileSystemStorage()
        self.config = config

    def create_config(
        self,
        query_id: int,
        query: dict,
        project_name: str | None = None,
        domain_area: str | None = None,
        model_group: str | None = None,
        github_branch: str | None = None,
        dbt_project_path: str | None = None,
    ) -> QueryConfiguration:
        """
        Create a new QueryConfiguration with computed naming.

        This should be called once when the user starts the wizard.
        Naming prefixes are computed here and never recomputed.

        Args:
            query_id: The query ID
            query: Query dictionary containing SQL and metadata
            project_name: Selected project/domain name
            github_branch: GitHub branch name (if using GitHub integration)
            dbt_project_path: Local dbt project path

        Returns:
            Created QueryConfiguration
        """
        logger.info(f"Creating QueryConfiguration for query {query_id}")

        # Compute naming ONCE here
        naming = self.compute_naming(project_name)

        # Create initial configuration
        config = QueryConfiguration(
            query_id=query_id,
            query_name=query.get("name", ""),
            project_name=project_name,
            domain_area=domain_area,
            model_group=model_group,
            naming=naming,
            github_branch=github_branch,
            dbt_project_path=dbt_project_path,
        )

        # Initialize model configurations from query tables
        tables = query.get("tables", [])
        config.model_configurations = self._init_model_configs(tables, naming, query.get("name", ""))

        # Save the configuration
        self.save_config(config)

        logger.info(f"Created QueryConfiguration for query {query_id} with {len(config.model_configurations)} models")

        return config

    def compute_naming(self, project_name: str | None = None) -> NamingConfiguration:
        """
        Compute naming prefixes from YAML config using NamingResolver.

        This method now delegates to NamingResolver for centralized naming logic,
        then converts the result to a NamingConfiguration object.

        Args:
            project_name: Optional project name for project-specific config

        Returns:
            NamingConfiguration with computed prefixes
        """
        # Start with defaults
        naming = NamingConfiguration()

        # If no config, return defaults
        if not self.config:
            return naming

        # Use NamingResolver to compute naming
        logger.info(f"Computing naming using NamingResolver for project: {project_name}")
        naming_resolver = NamingResolver(self.config)
        resolved = naming_resolver.resolve(
            query_naming=None,  # No query-specific overrides
            project_name=project_name,
        )

        # Convert ResolvedNaming to NamingConfiguration
        naming.staging_model_prefix = resolved.staging_model_prefix
        naming.intermediate_model_prefix = resolved.intermediate_model_prefix
        naming.mart_model_prefix = resolved.mart_model_prefix
        naming.case_style = resolved.case_style
        naming.separator = resolved.separator
        naming.source_name_from = resolved.source_name_from
        naming.use_layer_folders = resolved.use_layer_folders

        # Set layer_folder_names (single source of truth for folder names)
        # ResolvedNaming now has layer_folder_names dict, use it directly
        naming.layer_folder_names = resolved.layer_folder_names

        # Set additional fields that may come from config but aren't in ResolvedNaming
        # These are legacy fields that may still be needed
        naming.final_model_prefix = ""  # Deprecated in 2-layer architecture
        naming.final_model_suffix = ""  # Deprecated in 2-layer architecture

        # Replace {project} placeholder with actual project name
        if project_name:
            if "{project}" in naming.staging_model_prefix:
                naming.staging_model_prefix = naming.staging_model_prefix.replace("{project}", project_name)
            if "{project}" in naming.mart_model_prefix:
                naming.mart_model_prefix = naming.mart_model_prefix.replace("{project}", project_name)
            if "{project}" in naming.intermediate_model_prefix:
                naming.intermediate_model_prefix = naming.intermediate_model_prefix.replace("{project}", project_name)

        return naming

    def _init_model_configs(
        self,
        tables: list[str],
        naming: NamingConfiguration,
        query_name: str = "",
    ) -> list[ModelConfiguration]:
        """
        Initialize model configurations from query tables.

        Creates both intermediate and mart models for each table (2-layer architecture).

        Args:
            tables: List of table names from the query
            naming: Computed naming configuration
            query_name: Name of the query (for reference)

        Returns:
            List of ModelConfiguration objects
        """
        configs = []

        from dbt_training_wheels.utils.naming import build_model_name

        case_style = naming.case_style
        separator = naming.separator

        for table in tables:
            normalized_base = build_model_name(
                table,
                prefix="",
                suffix="",
                case_style=case_style,
                separator=separator,
            )
            # Intermediate model (contains transformation logic)
            intermediate_name = build_model_name(
                normalized_base,
                prefix=naming.intermediate_model_prefix,
                suffix="",
                case_style=case_style,
                separator=separator,
            )
            configs.append(
                ModelConfiguration(
                    table=intermediate_name,
                    model_type="intermediate",
                    materialization="table",
                    schema="",
                    tags=[],
                )
            )

            # Mart model (SELECT * FROM intermediate)
            mart_name = build_model_name(
                normalized_base,
                prefix=naming.mart_model_prefix,
                suffix=naming.final_model_suffix,
                case_style=case_style,
                separator=separator,
            )
            configs.append(
                ModelConfiguration(
                    table=mart_name,
                    model_type="mart",
                    materialization="table",
                    schema="",
                    tags=[],
                )
            )

        return configs

    def load_config(self, query_id: int) -> QueryConfiguration | None:
        """
        Load QueryConfiguration from temp file.

        Args:
            query_id: The query ID

        Returns:
            QueryConfiguration if exists, None otherwise
        """
        filename = f"query_config_{query_id}.json"
        content = self.storage.read_temp_file(filename)

        if not content:
            return None

        try:
            data = json.loads(content)
            return QueryConfiguration.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse QueryConfiguration for query {query_id}: {e}")
            return None

    def save_config(self, config: QueryConfiguration) -> bool:
        """
        Save QueryConfiguration to temp file.

        Args:
            config: The QueryConfiguration to save

        Returns:
            True if successful
        """
        config.update_timestamp()
        filename = f"query_config_{config.query_id}.json"
        content = json.dumps(config.to_dict(), indent=2)

        try:
            self.storage.save_temp_file(filename, content)
            logger.debug(f"Saved QueryConfiguration for query {config.query_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save QueryConfiguration: {e}")
            return False

    def load_or_create_config(
        self,
        query_id: int,
        query: dict,
        project_name: str | None = None,
        domain_area: str | None = None,
        model_group: str | None = None,
    ) -> QueryConfiguration:
        """
        Load existing config or create new one.

        Also handles conversion from old format files.

        Args:
            query_id: The query ID
            query: Query dictionary
            project_name: Selected project name e.g analytics
            domain_area: Selected project domain area e.g customer
        Returns:
            QueryConfiguration (loaded, converted, or created)
        """
        # Try to load existing config
        config = self.load_config(query_id)
        if config:
            logger.info(f"Loaded existing QueryConfiguration for query {query_id}")
            return config

        # Try to convert from old format
        config = self._convert_old_config(query_id, query, project_name, domain_area, model_group)
        if config:
            logger.info(f"Converted old config to QueryConfiguration for query {query_id}")
            return config

        # Create new config
        return self.create_config(query_id, query, project_name, domain_area, model_group)

    def _convert_old_config(
        self,
        query_id: int,
        query: dict,
        project_name: str | None = None,
        domain_area: str | None = None,
        model_group: str | None = None,
    ) -> QueryConfiguration | None:
        """
        Convert from old model_config and cross_project_refs files.

        After successful conversion, old files are deleted.

        Args:
            query_id: The query ID
            query: Query dictionary
            project_name: Selected project name

        Returns:
            Converted QueryConfiguration, or None if no old files found
        """
        # Check for old format files
        model_config = self.storage.load_model_config(query_id)
        cross_project_refs_content = self.storage.read_temp_file(f"cross_project_refs_{query_id}.json")

        if not model_config and not cross_project_refs_content:
            return None

        logger.info(f"Converting old config files for query {query_id}")

        # Compute naming
        naming = self.compute_naming(project_name)

        # Create base config
        config = QueryConfiguration(
            query_id=query_id,
            query_name=query.get("name", ""),
            project_name=project_name,
            domain_area=domain_area,
            model_group=model_group,
            naming=naming,
        )

        # Convert model configs
        if model_config:
            converted_configs = []
            for m in model_config:
                # Convert legacy "prep" type to "intermediate"
                model_type = m.get("type", "intermediate")
                if model_type == "prep":
                    model_type = "intermediate"

                converted_configs.append(
                    ModelConfiguration(
                        table=m.get("table", ""),
                        model_type=model_type,
                        materialization=m.get("materialization", "table"),
                        schema=m.get("schema", ""),
                        tags=m.get("tags", []),
                    )
                )
            config.model_configurations = converted_configs

        # Convert cross-project decisions
        if cross_project_refs_content:
            try:
                decisions = json.loads(cross_project_refs_content)
                config.cross_project_decisions = [
                    CrossProjectDecision(
                        original_reference=d.get("original_reference", ""),
                        use_cross_ref=d.get("use_cross_ref", False),
                        project=d.get("project"),
                        model=d.get("model"),
                    )
                    for d in decisions
                ]
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse cross_project_refs for query {query_id}")

        # Save converted config
        self.save_config(config)

        # Delete old files (user preference: delete after convert)
        self._delete_old_config_files(query_id)

        return config

    def _delete_old_config_files(self, query_id: int) -> None:
        """Delete old format config files after conversion."""
        from pathlib import Path

        try:
            # Delete model_config file
            if self.storage.model_config_exists(query_id):
                self.storage.delete_model_config(query_id)
                logger.info(f"Deleted old model_config_{query_id}.json")

            # Delete cross_project_refs file
            cross_ref_file = f"cross_project_refs_{query_id}.json"
            temp_dir = Path(self.storage.get_temp_directory())
            cross_ref_path = temp_dir / cross_ref_file
            if cross_ref_path.exists():
                cross_ref_path.unlink()
                logger.info(f"Deleted old {cross_ref_file}")

        except Exception as e:
            logger.warning(f"Failed to delete old config files for query {query_id}: {e}")

    def update_analysis_results(self, query_id: int, analysis_results: AnalysisResult) -> QueryConfiguration | None:
        """
        Update the analysis_results in the configuration.

        Args:
            query_id: The query ID
            analysis_results: Analysis results from analyze_query()

        Returns:
            Updated QueryConfiguration, or None if not found
        """
        config = self.load_config(query_id)
        if not config:
            logger.warning(f"No QueryConfiguration found for query {query_id}")
            return None

        from typing import Any, cast

        config.analysis_results = cast(dict[str, Any], analysis_results)
        self.save_config(config)
        return config

    def update_cross_project_decisions(self, query_id: int, decisions: list[dict]) -> QueryConfiguration | None:
        """
        Update cross-project reference decisions.

        Args:
            query_id: The query ID
            decisions: List of decision dictionaries

        Returns:
            Updated QueryConfiguration, or None if not found
        """
        config = self.load_config(query_id)
        if not config:
            logger.warning(f"No QueryConfiguration found for query {query_id}")
            return None

        config.cross_project_decisions = [CrossProjectDecision.from_dict(d) for d in decisions]
        config.step_completion.cross_project_refs_viewed = True
        self.save_config(config)

        logger.info(f"Updated {len(decisions)} cross-project decisions for query {query_id}")
        return config

    def update_model_configurations(
        self, query_id: int, models: list[dict], step: str | None = None
    ) -> QueryConfiguration | None:
        """
        Update model configurations (materialization, schema, tags).

        Merges updates into existing configurations - only updates
        fields that are provided.

        Args:
            query_id: The query ID
            models: List of model update dictionaries
            step: Optional step identifier for completion tracking

        Returns:
            Updated QueryConfiguration, or None if not found
        """
        config = self.load_config(query_id)
        if not config:
            logger.warning(f"No QueryConfiguration found for query {query_id}")
            return None

        # Create lookup for existing configs
        existing_map = {m.table: m for m in config.model_configurations}

        # Merge updates
        for update in models:
            table = update.get("table")
            if not table:
                continue

            if table in existing_map:
                existing = existing_map[table]
                if "materialization" in update:
                    existing.materialization = update["materialization"]
                if "schema" in update:
                    existing.schema = update["schema"]
                if "tags" in update:
                    existing.tags = update["tags"]
            else:
                # Add new model configuration
                config.model_configurations.append(ModelConfiguration.from_dict(update))

        # Update step completion
        if step == "materialization":
            config.step_completion.materialization_configured = True
        elif step == "tags":
            config.step_completion.tags_configured = True

        self.save_config(config)

        logger.info(f"Updated model configurations for query {query_id}")
        return config

    def update_step_completion(self, query_id: int, step_id: str, completed: bool = True) -> QueryConfiguration | None:
        """
        Update step completion state.

        Args:
            query_id: The query ID
            step_id: Step identifier
            completed: Whether the step is completed

        Returns:
            Updated QueryConfiguration, or None if not found
        """
        config = self.load_config(query_id)
        if not config:
            return None

        # Map step_id to StepCompletionState attribute
        step_map = {
            "analyze": "analyze_viewed",
            "cross_project_refs": "cross_project_refs_viewed",
            "final_models": "final_models_viewed",
            "materialization": "materialization_configured",
            "tags": "tags_configured",
            "sources": "sources_viewed",
            "review": "review_viewed",
            "deploy": "deployed",
        }

        attr = step_map.get(step_id)
        if attr and hasattr(config.step_completion, attr):
            setattr(config.step_completion, attr, completed)
            self.save_config(config)

        return config

    def delete_config(self, query_id: int) -> bool:
        """
        Delete QueryConfiguration for a query.

        Args:
            query_id: The query ID

        Returns:
            True if deleted successfully
        """
        from pathlib import Path

        filename = f"query_config_{query_id}.json"
        temp_dir = Path(self.storage.get_temp_directory())
        config_path = temp_dir / filename

        try:
            if config_path.exists():
                config_path.unlink()
                logger.info(f"Deleted QueryConfiguration for query {query_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete QueryConfiguration: {e}")
            return False


# Singleton instance for convenience
_service_instance: QueryConfigService | None = None


def get_query_config_service(
    config: Optional["OrganizationConfig"] = None,
) -> QueryConfigService:
    """
    Get the QueryConfigService singleton.

    Args:
        config: Optional OrganizationConfig to use

    Returns:
        QueryConfigService instance
    """
    global _service_instance

    if _service_instance is None or config is not None:
        _service_instance = QueryConfigService(config=config)

    return _service_instance
