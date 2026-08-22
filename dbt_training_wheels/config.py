"""Configuration management for DBT Training Wheels."""

import logging
import os

# Application configuration
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
HOST = "0.0.0.0"
PORT = 8000

# Directory paths
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))  # dbt_training_wheels package directory
BASE_DIR = os.path.dirname(PACKAGE_DIR)  # Project root directory
SQL_DIRECTORY = os.path.join(BASE_DIR, "source_sql_file")
TEMPLATE_DIRECTORY = os.path.join(PACKAGE_DIR, "templates")  # Inside package
STATIC_DIRECTORY = os.path.join(PACKAGE_DIR, "static")  # Inside package

# Conversion workflow steps
# Each step has:
#   - id: unique identifier (used in code, doesn't change)
#   - title: display name
#   - description: short description
#   - icon: icon name for UI
#   - enabled: whether step is shown in workflow (set to False to skip)
#   - file: JavaScript file name (without path, in static/js/steps/)
#   - renderFn: JavaScript function name to render this step
#
# To reorder steps, change the order in this list.
# To disable a step, set enabled: False.
# Display numbers are computed automatically from enabled steps.

CONVERSION_STEPS = [
    {
        "id": "analyze",
        "title": "Source Tables",
        "description": "Identify external tables and map to source() calls",
        "icon": "database",
        "enabled": True,
        "file": "analyze.js",
        "renderFn": "renderAnalyze",
    },
    # Layer-based steps (dynamically enabled based on naming config)
    # These come immediately after Analyze to show the decomposition
    {
        "id": "layer-staging",
        "title": "Staging Layer",
        "description": "CTEs that read from external sources",
        "icon": "layers",
        "enabled": True,  # Default enabled, dynamically controlled by naming config
        "file": "layer-staging.js",
        "renderFn": "renderLayerStaging",
        "layer": "staging",  # Metadata for dynamic enablement
    },
    {
        "id": "layer-intermediate",
        "title": "Intermediate Layer",
        "description": "All transformation logic (joins, filters, calculations)",
        "icon": "layers",
        "enabled": True,  # Default enabled, dynamically controlled by naming config
        "file": "layer-intermediate.js",
        "renderFn": "renderLayerIntermediate",
        "layer": "intermediate",  # Metadata for dynamic enablement
    },
    {
        "id": "layer-mart",
        "title": "Mart Layer",
        "description": "Final output tables - the business-facing models",
        "icon": "layers",
        "enabled": True,  # Default enabled, dynamically controlled by naming config
        "file": "layer-mart.js",
        "renderFn": "renderLayerMart",
        "layer": "mart",  # Metadata for dynamic enablement
    },
    {
        "id": "cross-project-refs",
        "title": "Cross-Project References",
        "description": "Detect references to models in other dbt projects",
        "icon": "git-branch",
        "enabled": True,
        "file": "cross-project-refs.js",
        "renderFn": "renderCrossProjectRefs",
    },
    {
        "id": "materialization",
        "title": "Materialization",
        "description": "Choose how dbt builds each model (table, view, incremental)",
        "icon": "settings",
        "enabled": True,
        "file": "materialization.js",
        "renderFn": "renderMaterialization",
    },
    {
        "id": "tags",
        "title": "Tags",
        "description": "Add tags for selective model execution and organization",
        "icon": "tag",
        "enabled": True,
        "file": "tags.js",
        "renderFn": "renderTags",
    },
    {
        "id": "sources",
        "title": "Define Sources",
        "description": "Map external tables to dbt sources",
        "icon": "database",
        "enabled": True,
        "file": "sources.js",
        "renderFn": "renderSources",
    },
    {
        "id": "review",
        "title": "Review",
        "description": "Review model lineage and configuration",
        "icon": "check-circle",
        "enabled": True,
        "file": "review.js",
        "renderFn": "renderReview",
    },
    {
        "id": "deploy",
        "title": "Deploy",
        "description": "Add models to your dbt project",
        "icon": "upload",
        "enabled": True,
        "file": "deploy.js",
        "renderFn": "renderDeploy",
    },
]

# Organization configuration
CONFIG_FILE_NAME = "dbt_training_wheels_config.yaml"
CONFIG_ENV_VAR = "DBT_TRAINING_WHEELS_CONFIG_PATH"

# Cached organization config - used as fallback for non-Flask contexts (CLI, tests).
# Flask routes should use current_app.org_config via get_org_config().
_org_config = None

logger = logging.getLogger(__name__)


def load_organization_config(config_path: str | None = None):
    """
    Load organization configuration from YAML file.

    Searches for config in this order:
    1. Explicit config_path parameter
    2. DBT_TRAINING_WHEELS_CONFIG_PATH environment variable
    3. dbt_training_wheels_config.yaml in current directory
    4. dbt_training_wheels_config.yaml in BASE_DIR

    If no config file is found, raises an error with instructions.

    Args:
        config_path: Optional explicit path to config file

    Returns:
        OrganizationConfig instance

    Raises:
        FileNotFoundError: If no config file is found
    """
    # Import here to avoid circular imports
    from dbt_training_wheels.config_schema import load_config_from_dict

    # Try to import yaml
    try:
        import yaml
    except ImportError as err:
        raise ImportError("PyYAML is required for DBT Training Wheels. Install it with: pip install pyyaml") from err

    # Determine config file path
    search_paths = []

    if config_path:
        search_paths.append(config_path)

    env_path = os.environ.get(CONFIG_ENV_VAR)
    if env_path:
        search_paths.append(env_path)

    search_paths.extend(
        [
            os.path.join(os.getcwd(), CONFIG_FILE_NAME),
            os.path.join(BASE_DIR, CONFIG_FILE_NAME),
        ]
    )

    # Try each path
    for path in search_paths:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    config_dict = yaml.safe_load(f)
                    if config_dict:
                        logger.info(f"Loaded organization config from: {path}")
                        return load_config_from_dict(config_dict)
            except Exception as e:
                logger.warning(f"Failed to load config from {path}: {e}")
                continue

    # No config file found - raise error with instructions
    example_path = os.path.join(BASE_DIR, "dbt_training_wheels_config.example.yaml")
    target_path = os.path.join(BASE_DIR, CONFIG_FILE_NAME)

    error_msg = (
        f"\n"
        f"══════════════════════════════════════════════════════════════\n"
        f"  DBT Training Wheels Configuration Required\n"
        f"══════════════════════════════════════════════════════════════\n"
        f"\n"
        f"  No configuration file found. Please create one:\n"
        f"\n"
        f"  cp {example_path} {target_path}\n"
        f"\n"
        f"  Then customize the settings for your organization.\n"
        f"══════════════════════════════════════════════════════════════\n"
    )
    raise FileNotFoundError(error_msg)


def get_org_config():
    """
    Get the organization configuration.

    Prefers Flask app context if available (for route handlers),
    falls back to cached global (for CLI, tests, non-Flask contexts).

    Returns:
        OrganizationConfig instance or None
    """
    # Try Flask context first (thread-safe for route handlers)
    try:
        from flask import current_app

        if current_app:
            config = getattr(current_app, "org_config", None)
            if config:
                return config
    except RuntimeError:
        # Not in Flask app context - fall through to global
        pass

    # Fall back to cached global
    global _org_config
    if _org_config is None:
        _org_config = load_organization_config()
    return _org_config


def set_org_config(config):
    """
    Set the organization configuration (for testing or dynamic updates).

    Args:
        config: OrganizationConfig instance
    """
    global _org_config
    _org_config = config


def get_project_config(project_name: str):
    """
    Get merged configuration for a specific project.

    Merges global defaults with project-specific settings.

    Args:
        project_name: Name of the project (e.g., "analytics")

    Returns:
        dict with merged configuration for the project
    """
    config = get_org_config()
    if not config:
        return None

    defaults = config.defaults
    project = config.projects.get(project_name)

    if not project:
        return None

    # Get project configs
    proj_dbt = project.dbt_config
    proj_github = proj_dbt.github if proj_dbt else None
    defaults_github = defaults.dbt_config.github

    return {
        "name": project_name,
        "github": {
            # dbt's github settings (from dbt_config)
            "enabled": defaults_github.enabled,
            "repository": defaults_github.repository,
            "default_branch": defaults_github.default_branch,
            "branch_prefix": defaults_github.branch_prefix,
            "token": defaults_github.token,
            "auto_create_pr": defaults_github.auto_create_pr,
            "pr_title_prefix": defaults_github.pr_title_prefix,
            "pr_labels": defaults_github.pr_labels,
            "base_path": proj_github.base_path if proj_github else defaults_github.base_path,
        },
    }


def get_available_projects():
    """
    Get list of available project names from config.

    Returns:
        list of project names
    """
    config = get_org_config()
    if not config or not config.projects:
        return []
    return list(config.projects.keys())


def get_conversion_steps(config=None) -> list[dict]:
    """
    Get conversion steps from config or fall back to Python defaults.

    This allows organizations to customize workflow steps via dbt_training_wheels_config.yaml
    while maintaining backwards compatibility with the default Python-defined steps.

    Layer-based steps (intermediate, mart) are dynamically enabled based
    on whether the corresponding prefix is defined in the naming configuration.
    The 2-layer architecture always includes both intermediate and mart layers.

    Args:
        config: Optional OrganizationConfig. If not provided, uses cached config.

    Returns:
        List of step dictionaries with id, title, description, icon, enabled, file, renderFn
    """
    # Use provided config or try to get cached config
    if config is None:
        try:
            config = get_org_config()
        except FileNotFoundError:
            # No config file - use Python defaults
            return CONVERSION_STEPS

    # Check if config has workflow steps defined
    if config and config.workflow and config.workflow.steps:
        # Convert dataclass instances to dicts for JavaScript compatibility
        steps = []
        for step in config.workflow.steps:
            steps.append(
                {
                    "id": step.id,
                    "title": step.title,
                    "description": step.description,
                    "icon": step.icon,
                    "enabled": step.enabled,
                    "file": step.file,
                    "renderFn": step.renderFn,
                }
            )

        # Apply custom ordering if specified
        if config.workflow.step_order:
            order_map = {step_id: idx for idx, step_id in enumerate(config.workflow.step_order)}
            steps.sort(key=lambda s: order_map.get(s["id"], 999))

        return steps

    # Use Python defaults and apply dynamic layer step enablement
    steps = _apply_dynamic_layer_steps(CONVERSION_STEPS, config)
    return steps


def _apply_dynamic_layer_steps(steps: list[dict], config) -> list[dict]:
    """
    Apply dynamic enablement to layer-based steps based on naming configuration.

    In the 2-layer architecture:
    - layer-intermediate: enabled if intermediate_model_prefix is defined (always enabled by default)
    - layer-mart: enabled if mart_model_prefix is defined (always enabled by default)

    Args:
        steps: List of step dictionaries (CONVERSION_STEPS)
        config: OrganizationConfig instance

    Returns:
        List of step dictionaries with layer steps dynamically enabled/disabled
    """
    import copy

    # Deep copy to avoid modifying the original
    result_steps = copy.deepcopy(steps)

    # Get naming config from organization config
    # Check multiple locations: config.naming, config.defaults.dbt_config.naming
    naming = None
    if config and hasattr(config, "naming") and config.naming:
        naming = config.naming
    elif config and hasattr(config, "defaults") and config.defaults:
        if hasattr(config.defaults, "dbt_config") and config.defaults.dbt_config:
            if hasattr(config.defaults.dbt_config, "naming") and config.defaults.dbt_config.naming:
                naming = config.defaults.dbt_config.naming

    # 3-layer architecture: STAGING + INT + MART
    layer_enabled = {
        "staging": True,  # STG layer - external source combinations
        "intermediate": True,  # INT layer - transformations
        "mart": True,  # MART layer - final output
    }

    if naming:
        # Enable staging if staging_model_prefix is defined and not empty
        layer_enabled["staging"] = bool(naming.staging_model_prefix and naming.staging_model_prefix.strip())

        # Enable intermediate if intermediate_model_prefix is defined and not empty
        layer_enabled["intermediate"] = bool(
            naming.intermediate_model_prefix and naming.intermediate_model_prefix.strip()
        )

        # Mart is always enabled as it represents final output
        layer_enabled["mart"] = True

    # Apply enablement to layer steps
    for step in result_steps:
        layer = step.get("layer")
        if layer and layer in layer_enabled:
            step["enabled"] = layer_enabled[layer]

    logger.info(
        f"[Layer Steps] 3-layer architecture - staging={layer_enabled['staging']}, "
        f"intermediate={layer_enabled['intermediate']}, mart={layer_enabled['mart']}"
    )

    return result_steps
