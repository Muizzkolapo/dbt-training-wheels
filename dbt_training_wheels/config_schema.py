"""
Organization Configuration Schema for DBT Training Wheels.

This module defines the configuration options that organizations can customize
to match their naming conventions, database dialects, and dbt project structure.
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ModelNamingConfig:
    """Configuration for how dbt model files should be named.

    layer_folder_names is the single source of truth for folder names.
    Individual folder properties (staging_folder, etc.) are computed from this dict.
    """

    # Prefix/suffix options (2-layer architecture: INT + MART)
    staging_model_prefix: str = "stg__"  # e.g., stg__customers.sql
    intermediate_model_prefix: str = "int__"  # e.g., int__customers.sql
    mart_model_prefix: str = ""  # e.g., fct_sales.sql, dim_customers.sql

    # Naming style options
    case_style: Literal["lowercase", "uppercase", "snake_case", "camelCase", "PascalCase"] = "snake_case"
    separator: Literal["_", "-", ""] = "_"  # Word separator

    # Source naming
    source_name_from: Literal["dataset", "schema", "custom"] = "dataset"
    custom_source_name: str | None = None

    # Whether to include schema/dataset in model file names
    # When True: stg__schema__table.sql, When False: stg__table.sql
    include_schema_in_model_name: bool = True

    # Model layer structure (2-layer: INT + MART)
    use_layer_folders: bool = True  # models/intermediate/, models/marts/

    # Single source of truth for folder names
    layer_folder_names: dict[str, str] = field(
        default_factory=lambda: {
            "staging": "staging",
            "intermediate": "intermediate",
            "mart": "mart",
        }
    )

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
        """Get mart folder name from layer_folder_names dict."""
        return self.layer_folder_names.get("mart", "mart")


@dataclass
class DatabaseDialectConfig:
    """Configuration for database-specific SQL parsing."""

    # Database type
    dialect: Literal["bigquery", "postgres", "mysql", "oracle", "snowflake", "redshift", "databricks"] = "bigquery"

    # Table reference patterns
    table_quote_style: Literal["backtick", "double_quote", "square_bracket", "none"] = "backtick"
    fully_qualified_format: str = "project.dataset.table"  # or "database.schema.table"

    # Project/database identifiers
    default_project: str | None = None  # e.g., "my-gcp-project"
    default_dataset: str | None = None  # e.g., "analytics"
    default_schema: str | None = None  # e.g., "public"

    # System schemas to exclude from source detection
    system_schemas: list[str] = field(
        default_factory=lambda: [
            "information_schema",
            "pg_catalog",
            "sys",
            "mysql",
            "performance_schema",
            "INFORMATION_SCHEMA",
        ]
    )

    # System table patterns (regex)
    system_table_patterns: list[str] = field(
        default_factory=lambda: [
            r"^pg_",  # PostgreSQL system tables
            r"^sql_",  # MySQL system tables
            r"^v\$",  # Oracle dynamic views
            r"^dba_",  # Oracle DBA views
            r"__TABLES__$",  # BigQuery metadata
            r"^INFORMATION_SCHEMA\.",
        ]
    )


@dataclass
class SQLParserConfig:
    """Configuration for SQL parsing behavior."""

    # Metadata extraction from comments
    metadata_patterns: dict[str, str] = field(
        default_factory=lambda: {
            "name": r"--\s*name:\s*(.+)",
            "dataset": r"--\s*dataset:\s*(.+)",
            "schedule": r"--\s*[Ss]chedule:\s*(.+)",
            "description": r"--\s*[Dd]escription:\s*(.+)",
        }
    )

    # How to handle different SQL patterns
    extract_ctes_as_models: bool = True  # Convert CTEs to prep models
    max_cte_models: int = 5  # Limit CTEs extracted
    preserve_comments: bool = True  # Keep comments in output


@dataclass
class SourceConfig:
    """Configuration for dbt source() generation."""

    # Source file settings
    sources_file_name: str = "sources.yml"
    sources_file_location: str = "models/"  # or "models/staging/"

    # Source properties
    include_freshness: bool = False

    # Documentation
    include_descriptions: bool = True


@dataclass
class OutputConfig:
    """Configuration for generated file output."""

    # File structure
    output_directory: str = "dbt_models"
    create_subdirectories: bool = True

    # File contents
    include_config_block: bool = True
    default_materialization: Literal["view", "table", "incremental", "ephemeral"] = "table"
    include_source_comment: bool = True  # "-- Source: original_query_name"

    # README generation
    generate_readme: bool = True


@dataclass
class TagsConfig:
    """Configuration for dbt model tags."""

    # Available tags that appear as selectable chips in the UI
    available_tags: list[str] = field(
        default_factory=lambda: [
            "daily",
            "weekly",
            "monthly",
            "critical",
            "standard",
            "intermediate",
            "marts",
        ]
    )

    # Default tags to pre-select for new models
    default_tags: list[str] = field(default_factory=list)

    # Allow users to add custom tags not in the available list
    allow_custom_tags: bool = True


@dataclass
class DbtProjectConfig:
    """Configuration for direct dbt project integration."""

    # Root path to the dbt project (single project - legacy)
    project_path: str | None = None

    # List of allowed project paths (multiple projects)
    allowed_projects: list[str] = field(default_factory=list)

    # Path to models directory (relative to project_path or absolute)
    models_path: str = "models/source"

    # Whether to automatically write files to dbt project on generation
    auto_write_enabled: bool = False


@dataclass
class GitHubConfig:
    """Configuration for GitHub integration - push files directly to a branch."""

    # Enable GitHub integration
    enabled: bool = False

    # GitHub repository in format "owner/repo"
    repository: str = ""

    # Default base branch (usually main or master)
    default_branch: str = "main"

    # Branch prefix for generated branches (e.g., "dbt_training_wheels/" -> "dbt_training_wheels/churn_model")
    branch_prefix: str = "dbt_training_wheels/"

    # Base path within the repo where the dbt project lives
    # e.g., "dbt_projects/analytics" if your models are at
    # dbt_projects/analytics/models/...
    base_path: str = ""

    # GitHub token (loaded from environment variable GITHUB_TOKEN if not set)
    token: str | None = None

    # Auto-create PR after pushing
    auto_create_pr: bool = False

    # PR settings
    pr_title_prefix: str = "[DBT Training Wheels] "
    pr_labels: list[str] = field(default_factory=lambda: ["dbt_training_wheels"])


# ============================================
# PROJECT-CENTRIC CONFIG STRUCTURE (v2.0)
# ============================================


@dataclass
class GitHubProjectConfig:
    """Per-project GitHub configuration overrides."""

    # Base path within the repo where the dbt project lives
    base_path: str = ""


@dataclass
class DomainConfig:
    """A business domain within a dbt project.

    Domains map destination datasets to a review/ownership boundary. A conversion whose
    models write to datasets in more than one domain is a cross-domain conversion, and
    can be deployed as a stack of dependent PRs instead of one flat PR.
    """

    # Domain name (matches the key in the domains dict)
    name: str = ""

    # Destination datasets owned by this domain, e.g. "my_dataset"
    datasets: list[str] = field(default_factory=list)


@dataclass
class ProjectDbtConfig:
    """Per-project dbt configuration (under dbt_config key)."""

    database: DatabaseDialectConfig | None = None
    naming: ModelNamingConfig | None = None
    tags: TagsConfig | None = None
    github: GitHubProjectConfig | None = None  # dbt's github settings

    # GCP projects containing scheduled queries (not yet converted to dbt)
    scheduled_query_projects: list[str] = field(default_factory=list)

    # Business domains, keyed by name in YAML
    domains: list[DomainConfig] = field(default_factory=list)


@dataclass
class ProjectConfig:
    """Configuration for a single dbt project."""

    # Project name (matches the key in projects dict)
    name: str = ""

    # dbt-related settings (under dbt_config key)
    dbt_config: ProjectDbtConfig | None = None


@dataclass
class GitHubDefaultsConfig:
    """Default GitHub settings (global)."""

    # Enable GitHub integration
    enabled: bool = False

    # GitHub repository in format "owner/repo"
    repository: str = ""

    # Default base branch
    default_branch: str = "main"

    # Branch prefix for generated branches
    branch_prefix: str = "dbt_training_wheels/"

    # Base path within the repo
    base_path: str = ""

    # GitHub token
    token: str | None = None

    # Auto-create PR after pushing
    auto_create_pr: bool = False

    # PR settings
    pr_title_prefix: str = "[DBT Training Wheels] "
    pr_labels: list[str] = field(default_factory=lambda: ["dbt_training_wheels"])


@dataclass
class DbtDefaultsConfig:
    """Default dbt configuration (under defaults.dbt_config)."""

    database: DatabaseDialectConfig = field(default_factory=DatabaseDialectConfig)
    naming: ModelNamingConfig | None = None  # Default naming settings (optional)
    github: GitHubDefaultsConfig = field(default_factory=GitHubDefaultsConfig)  # dbt's github settings


@dataclass
class DefaultsConfig:
    """Global default configuration - minimal settings only."""

    # dbt-related defaults (under dbt_config key)
    dbt_config: DbtDefaultsConfig = field(default_factory=DbtDefaultsConfig)


@dataclass
class CrossProjectRefProject:
    """Configuration for a known dbt project (for cross-project reference detection)."""

    # dbt project name (used in ref() calls, e.g., "analytics_platform")
    name: str = ""

    # BigQuery datasets that contain models from this project
    # Used by DatasetResolver to match dataset names to projects
    datasets: list[str] = field(default_factory=list)

    # GCP projects that are valid sources for this dbt project
    # Only tables from these GCP projects will be matched for cross-project refs
    # If empty, matches ANY GCP project (backward compatible)
    source_projects: list[str] = field(default_factory=list)

    # GitHub base path to the dbt project (for scanning public models via GitHub)
    # Same format as github.base_path, e.g., "dbt_projects/analytics_platform"
    github_base_path: str | None = None

    # Future: path to manifest.json for exact model matching
    # manifest_path: Optional[str] = None


@dataclass
class CrossProjectConfig:
    """Configuration for cross-project reference detection (dbt Mesh support)."""

    # Enable/disable cross-project reference detection
    enabled: bool = False

    # Resolver type: "dataset" (MVP), "manifest" (future), "hybrid" (future)
    resolver: str = "dataset"

    # Known dbt projects and their datasets
    projects: list[CrossProjectRefProject] = field(default_factory=list)


@dataclass
class TemplatesConfig:
    """Configuration for template paths."""

    # Custom path to dbt output templates (final_model.sql.j2, etc.)
    # If not set, uses built-in templates in dbt_training_wheels/templates/dbt/
    dbt_template_path: str | None = None


@dataclass
class WorkflowStepConfig:
    """Configuration for a single workflow step."""

    id: str  # Unique identifier (used in code)
    title: str  # Display name
    description: str  # Short description
    icon: str = "file"  # Icon name for UI
    enabled: bool = True  # Whether step is shown in workflow
    file: str = ""  # JavaScript file name (without path)
    renderFn: str = ""  # JavaScript function name to render this step


@dataclass
class WorkflowConfig:
    """Configuration for the conversion workflow."""

    # List of workflow steps
    steps: list[WorkflowStepConfig] = field(default_factory=list)

    # Allow custom step ordering (list of step IDs)
    step_order: list[str] | None = None


@dataclass
class OrganizationConfig:
    """
    Complete organization configuration for DBT Training Wheels.

    Organizations can customize this to match their:
    - Naming conventions
    - Database platform
    - dbt project structure
    - Team preferences

    Config structure v2.0:
    - defaults: Global default settings for all projects
    - projects: Per-project configuration overrides
    """

    # Organization metadata
    org_name: str | None = None
    config_version: str = "1.0"

    # Global defaults and per-project configs
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    projects: dict[str, ProjectConfig] = field(default_factory=dict)

    # Legacy sub-configurations (kept for backward compat, prefer defaults.*)
    naming: ModelNamingConfig = field(default_factory=ModelNamingConfig)
    database: DatabaseDialectConfig = field(default_factory=DatabaseDialectConfig)
    parser: SQLParserConfig = field(default_factory=SQLParserConfig)
    sources: SourceConfig = field(default_factory=SourceConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    tags: TagsConfig = field(default_factory=TagsConfig)
    dbt_project: DbtProjectConfig = field(default_factory=DbtProjectConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    cross_project_refs: CrossProjectConfig = field(default_factory=CrossProjectConfig)

    # Workflow and templates configuration
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    templates: TemplatesConfig | None = None

    # Custom rules (advanced)
    custom_table_mappings: dict[str, str] = field(default_factory=dict)
    # e.g., {"raw_customers": "stg_customers", "legacy.orders": "stg_orders"}

    ignored_tables: list[str] = field(default_factory=list)
    # Tables to skip during conversion

    # dbt project settings (legacy)
    dbt_project_name: str | None = None
    dbt_version: str = "1.0"


# Pre-configured templates for common setups
BIGQUERY_CONFIG = OrganizationConfig(
    database=DatabaseDialectConfig(
        dialect="bigquery", table_quote_style="backtick", fully_qualified_format="project.dataset.table"
    )
)

POSTGRES_CONFIG = OrganizationConfig(
    database=DatabaseDialectConfig(
        dialect="postgres",
        table_quote_style="double_quote",
        fully_qualified_format="schema.table",
        system_schemas=["pg_catalog", "information_schema", "pg_toast"],
    )
)

SNOWFLAKE_CONFIG = OrganizationConfig(
    database=DatabaseDialectConfig(
        dialect="snowflake", table_quote_style="double_quote", fully_qualified_format="database.schema.table"
    )
)

MYSQL_CONFIG = OrganizationConfig(
    database=DatabaseDialectConfig(
        dialect="mysql",
        table_quote_style="backtick",
        fully_qualified_format="database.table",
        system_schemas=["mysql", "sys", "information_schema", "performance_schema"],
    )
)


def get_default_config(dialect: str = "bigquery") -> OrganizationConfig:
    """Get a default configuration for a specific database dialect."""
    configs = {
        "bigquery": BIGQUERY_CONFIG,
        "postgres": POSTGRES_CONFIG,
        "postgresql": POSTGRES_CONFIG,
        "snowflake": SNOWFLAKE_CONFIG,
        "mysql": MYSQL_CONFIG,
    }
    return configs.get(dialect.lower(), BIGQUERY_CONFIG)


VALID_DIALECTS = ["bigquery", "postgres", "mysql", "oracle", "snowflake", "redshift", "databricks"]
VALID_MATERIALIZATIONS = ["view", "table", "incremental", "ephemeral"]
VALID_CASE_STYLES = ["lowercase", "uppercase", "snake_case", "camelCase", "PascalCase"]

# Configuration versioning
CURRENT_CONFIG_VERSION = "1.0"
SUPPORTED_CONFIG_VERSIONS = ["1.0"]


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""

    pass


class ConfigMigrationError(Exception):
    """Raised when configuration migration fails."""

    pass


def _migrate_config_v1_0_to_v1_1(config_dict: dict) -> dict:
    """
    Migrate config from v1.0 to v1.1.

    v1.1 restructured dag_factory, which no longer exists - the whole section is
    ignored on load now. The step is kept so a v1.0 config still gets its version
    marker bumped, and so the migration chain below has something to be a chain of.
    """
    return config_dict


def migrate_config(config_dict: dict) -> dict:
    """
    Migrate configuration to the current version.

    Args:
        config_dict: Configuration dictionary (may be old version)

    Returns:
        Migrated configuration dictionary

    Raises:
        ConfigMigrationError: If migration fails
    """
    import logging

    logger = logging.getLogger(__name__)

    version = config_dict.get("config_version", "1.0")

    if version == CURRENT_CONFIG_VERSION:
        return config_dict

    if version not in SUPPORTED_CONFIG_VERSIONS:
        raise ConfigMigrationError(
            f"Unsupported config version '{version}'. Supported versions: {', '.join(SUPPORTED_CONFIG_VERSIONS)}"
        )

    # Apply migrations in order
    conversions = [
        ("1.0", "1.1", _migrate_config_v1_0_to_v1_1),
    ]

    current_version = version
    for from_ver, to_ver, convert_fn in conversions:
        if current_version == from_ver:
            logger.info(f"Migrating config from v{from_ver} to v{to_ver}")
            config_dict = convert_fn(config_dict)
            config_dict["config_version"] = to_ver
            current_version = to_ver

    return config_dict


def _validate_config_dict(config_dict: dict) -> None:
    """
    Validate configuration values before loading.

    Args:
        config_dict: Configuration dictionary to validate

    Raises:
        ConfigValidationError: If validation fails
    """
    # Validate database dialect
    if "database" in config_dict:
        dialect = config_dict["database"].get("dialect", "")
        if dialect and dialect not in VALID_DIALECTS:
            raise ConfigValidationError(
                f"Invalid database dialect '{dialect}'. Must be one of: {', '.join(VALID_DIALECTS)}"
            )

    # Validate output materialization
    if "output" in config_dict:
        materialization = config_dict["output"].get("default_materialization", "")
        if materialization and materialization not in VALID_MATERIALIZATIONS:
            raise ConfigValidationError(
                f"Invalid default_materialization '{materialization}'. "
                f"Must be one of: {', '.join(VALID_MATERIALIZATIONS)}"
            )

    # Validate naming case style
    if "naming" in config_dict:
        case_style = config_dict["naming"].get("case_style", "")
        if case_style and case_style not in VALID_CASE_STYLES:
            raise ConfigValidationError(
                f"Invalid case_style '{case_style}'. Must be one of: {', '.join(VALID_CASE_STYLES)}"
            )

    # Validate GitHub repository format
    if "github" in config_dict:
        github = config_dict["github"]
        if github.get("enabled") and github.get("repository"):
            repo = github["repository"]
            if "/" not in repo or len(repo.split("/")) != 2:
                raise ConfigValidationError(f"Invalid GitHub repository format '{repo}'. Expected format: 'owner/repo'")


def load_config_from_dict(config_dict: dict) -> OrganizationConfig:
    """Load organization config from a dictionary (e.g., from JSON/YAML)."""
    # Convert old config versions to current
    config_dict = migrate_config(config_dict)

    # Validate configuration before loading
    _validate_config_dict(config_dict)

    naming = ModelNamingConfig(**config_dict.get("naming", {}))
    database = DatabaseDialectConfig(**config_dict.get("database", {}))
    parser = SQLParserConfig(**config_dict.get("parser", {}))
    sources = SourceConfig(**config_dict.get("sources", {}))
    output = OutputConfig(**config_dict.get("output", {}))
    tags = TagsConfig(**config_dict.get("tags", {}))
    dbt_project = DbtProjectConfig(**config_dict.get("dbt_project", {}))

    # GitHub config can come from root or from defaults.dbt_config
    github_dict = config_dict.get("github", {})
    if not github_dict and "defaults" in config_dict:
        # Fall back to defaults.dbt_config.github if root github not specified
        dbt_defaults = config_dict["defaults"].get("dbt_config", {})
        github_dict = dbt_defaults.get("github", {})
    github = GitHubConfig(**github_dict)

    # Parse cross_project_refs config (if present)
    cross_project_refs = CrossProjectConfig()
    if "cross_project_refs" in config_dict:
        cpr_dict = config_dict["cross_project_refs"].copy()
        # Parse projects list if present
        if "projects" in cpr_dict and cpr_dict["projects"]:
            cpr_dict["projects"] = [CrossProjectRefProject(**proj) for proj in cpr_dict["projects"]]
        cross_project_refs = CrossProjectConfig(**cpr_dict)

    # Parse workflow steps (if present in config)
    workflow = WorkflowConfig()
    if "workflow" in config_dict:
        workflow_dict = config_dict["workflow"]
        if "steps" in workflow_dict and workflow_dict["steps"]:
            workflow.steps = [WorkflowStepConfig(**step) for step in workflow_dict["steps"]]
        if "step_order" in workflow_dict:
            workflow.step_order = workflow_dict["step_order"]

    # Parse templates config (if present)
    templates = None
    if "templates" in config_dict:
        templates = TemplatesConfig(**config_dict["templates"])

    # Parse v2.0 defaults config (if present)
    defaults = DefaultsConfig()
    if "defaults" in config_dict:
        defaults_dict = config_dict["defaults"]

        # Parse dbt_config section in defaults
        dbt_defaults = DbtDefaultsConfig()
        if "dbt_config" in defaults_dict:
            dbt_dict = defaults_dict["dbt_config"]
            dbt_defaults = DbtDefaultsConfig(
                database=DatabaseDialectConfig(**dbt_dict.get("database", {})),
                naming=ModelNamingConfig(**dbt_dict["naming"]) if "naming" in dbt_dict else None,
                github=GitHubDefaultsConfig(**dbt_dict.get("github", {})),
            )

        defaults = DefaultsConfig(dbt_config=dbt_defaults)

    # Parse v2.0 projects config (if present)
    projects: dict[str, ProjectConfig] = {}
    if "projects" in config_dict:
        for project_name, project_dict in config_dict["projects"].items():
            project_config = ProjectConfig(name=project_name)

            # Parse dbt_config section for this project
            if "dbt_config" in project_dict:
                dbt_dict = project_dict["dbt_config"]
                project_config.dbt_config = ProjectDbtConfig(
                    database=DatabaseDialectConfig(**dbt_dict["database"]) if "database" in dbt_dict else None,
                    naming=ModelNamingConfig(**dbt_dict["naming"]) if "naming" in dbt_dict else None,
                    tags=TagsConfig(**dbt_dict["tags"]) if "tags" in dbt_dict else None,
                    github=GitHubProjectConfig(**dbt_dict["github"]) if "github" in dbt_dict else None,
                    scheduled_query_projects=dbt_dict.get("scheduled_query_projects", []),
                    domains=[
                        DomainConfig(name=domain_name, datasets=(domain_dict or {}).get("datasets", []))
                        for domain_name, domain_dict in (dbt_dict.get("domains") or {}).items()
                    ],
                )

            projects[project_name] = project_config

    return OrganizationConfig(
        org_name=config_dict.get("org_name"),
        config_version=config_dict.get("config_version", "2.0"),
        defaults=defaults,
        projects=projects,
        naming=naming,
        database=database,
        parser=parser,
        sources=sources,
        output=output,
        tags=tags,
        dbt_project=dbt_project,
        github=github,
        cross_project_refs=cross_project_refs,
        workflow=workflow,
        templates=templates,
        custom_table_mappings=config_dict.get("custom_table_mappings", {}),
        ignored_tables=config_dict.get("ignored_tables", []),
        dbt_project_name=config_dict.get("dbt_project_name"),
        dbt_version=config_dict.get("dbt_version", "1.0"),
    )
