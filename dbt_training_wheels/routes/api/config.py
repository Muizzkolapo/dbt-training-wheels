"""Configuration API routes for DBT Training Wheels.

Endpoints for configuration management and dbt project detection.
"""

import logging
import os
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from dbt_training_wheels.config import get_available_projects, get_org_config, get_project_config
from dbt_training_wheels.config_schema import get_default_config
from dbt_training_wheels.exceptions import ConfigurationError, FileSystemError, ValidationError
from dbt_training_wheels.utils import handle_route_errors

logger = logging.getLogger(__name__)

config_bp = Blueprint("config", __name__)


@config_bp.route("/config")
def get_config_endpoint():
    """
    Get current organization configuration.

    Returns:
        JSON response with current config settings
    """
    config = get_org_config()
    if not config:
        config = get_default_config()

    # Get available projects from v1.0 config
    available_projects = get_available_projects()
    first_project = available_projects[0] if available_projects else None
    first_project_config = get_project_config(first_project) if first_project else None

    # Get defaults from v1.0 structure
    defaults = config.defaults
    defaults_github = defaults.dbt_config.github

    # Convert dataclass to dict for JSON serialization
    config_dict = {
        "org_name": config.org_name,
        "config_version": config.config_version,
        "projects": available_projects,
        "default_project": first_project,
        "naming": {
            "staging_model_prefix": config.naming.staging_model_prefix,
            "intermediate_model_prefix": config.naming.intermediate_model_prefix,
            "mart_model_prefix": config.naming.mart_model_prefix,
            "case_style": config.naming.case_style,
            "separator": config.naming.separator,
            "source_name_from": config.naming.source_name_from,
            "use_layer_folders": config.naming.use_layer_folders,
            "layer_folder_names": config.naming.layer_folder_names,
        },
        "database": {
            "dialect": defaults.dbt_config.database.dialect,
            "table_quote_style": config.database.table_quote_style,
            "fully_qualified_format": config.database.fully_qualified_format,
            "default_project": config.database.default_project,
            "default_dataset": config.database.default_dataset,
            "default_schema": config.database.default_schema,
        },
        "parser": {
            "extract_ctes_as_models": config.parser.extract_ctes_as_models,
            "max_cte_models": config.parser.max_cte_models,
            "preserve_comments": config.parser.preserve_comments,
        },
        "output": {
            "output_directory": config.output.output_directory,
            "create_subdirectories": config.output.create_subdirectories,
            "include_config_block": config.output.include_config_block,
            "default_materialization": config.output.default_materialization,
            "include_source_comment": config.output.include_source_comment,
            "generate_readme": config.output.generate_readme,
        },
        "sources": {
            "sources_file_name": config.sources.sources_file_name,
            "sources_file_location": config.sources.sources_file_location,
            "include_freshness": config.sources.include_freshness,
            "include_descriptions": config.sources.include_descriptions,
        },
        "tags": {
            "available_tags": config.tags.available_tags,
            "default_tags": config.tags.default_tags,
            "allow_custom_tags": config.tags.allow_custom_tags,
        },
        "dbt_project_name": config.dbt_project_name,
        "dbt_version": config.dbt_version,
        "dbt_project": {
            "project_path": os.getcwd(),
            "models_path": config.dbt_project.models_path if config.dbt_project else "models",
            "auto_write_enabled": config.dbt_project.auto_write_enabled if config.dbt_project else False,
        },
        # GitHub config from defaults.dbt_config.github (v1.0 structure)
        "github": {
            "enabled": defaults_github.enabled,
            "repository": defaults_github.repository,
            "default_branch": defaults_github.default_branch,
            "branch_prefix": defaults_github.branch_prefix,
            "auto_create_pr": defaults_github.auto_create_pr,
            "auth_method": "ssh",  # All GitHub operations use SSH keys
            "base_path": first_project_config["github"]["base_path"] if first_project_config else "",
        },
    }

    return jsonify(config_dict)


@config_bp.route("/config/dbt-project-path", methods=["POST"])
@handle_route_errors
def save_dbt_project_path_endpoint():
    """
    Save the dbt project path to the config file.

    Request Body:
        {
            "project_path": "/path/to/dbt/project"
        }

    Returns:
        JSON response with success status
    """
    import yaml

    if not request.json:
        raise ValidationError.missing_field("request body")

    project_path = request.json.get("project_path", "").strip()

    if not project_path:
        raise ValidationError(
            user_message="Please provide a dbt project path",
            beginner_help="The path to your dbt project is required",
            common_fixes=["Enter the full path to your dbt project folder", "Example: /Users/yourname/dbt-project"],
            docs_anchor="configuration-errors",
            technical_message="project_path is empty",
        )

    # Validate the path exists
    if not os.path.exists(project_path):
        raise FileSystemError(
            user_message=f"The path '{project_path}' does not exist",
            beginner_help="Make sure you entered the correct path to your dbt project",
            common_fixes=[
                "Check the path for typos",
                "Make sure the folder exists",
                f"Create the folder: mkdir -p {project_path}",
            ],
            docs_anchor="file-errors",
            technical_message=f"Path does not exist: {project_path}",
        )

    # Config file is expected at cwd (where dbt_training_wheels is run from)
    config_path = os.path.join(os.getcwd(), "dbt_training_wheels_config.yaml")

    try:
        # Read existing config
        with open(config_path) as f:
            config_content = yaml.safe_load(f) or {}

        # Update the dbt_project section
        if "dbt_project" not in config_content:
            config_content["dbt_project"] = {}

        config_content["dbt_project"]["project_path"] = project_path

        # Write back
        with open(config_path, "w") as f:
            yaml.dump(config_content, f, default_flow_style=False, sort_keys=False)

        # Reload the config in the app
        from dbt_training_wheels.config import load_organization_config

        current_app.org_config = load_organization_config()

        return jsonify({"success": True, "message": "dbt project path saved", "project_path": project_path})

    except PermissionError as err:
        raise FileSystemError.permission_denied(config_path) from err
    except Exception as e:
        raise ConfigurationError(
            user_message="We couldn't save the configuration",
            beginner_help="There was an issue updating the config file",
            common_fixes=["Check file permissions for dbt_training_wheels_config.yaml", "Try restarting the application"],
            docs_anchor="configuration-errors",
            technical_message=f"Failed to update config: {str(e)}",
        ) from e


@config_bp.route("/config/presets")
def get_config_presets_endpoint():
    """
    Get available configuration presets.

    Returns:
        JSON response with available preset names and descriptions
    """
    presets = {
        "bigquery": {
            "name": "BigQuery",
            "description": "Google BigQuery with backtick quoting",
            "dialect": "bigquery",
            "table_quote_style": "backtick",
            "fully_qualified_format": "project.dataset.table",
        },
        "postgres": {
            "name": "PostgreSQL",
            "description": "PostgreSQL with double-quote quoting",
            "dialect": "postgres",
            "table_quote_style": "double_quote",
            "fully_qualified_format": "schema.table",
        },
        "snowflake": {
            "name": "Snowflake",
            "description": "Snowflake with double-quote quoting",
            "dialect": "snowflake",
            "table_quote_style": "double_quote",
            "fully_qualified_format": "database.schema.table",
        },
        "mysql": {
            "name": "MySQL",
            "description": "MySQL with backtick quoting",
            "dialect": "mysql",
            "table_quote_style": "backtick",
            "fully_qualified_format": "database.table",
        },
    }

    return jsonify(presets)


@config_bp.route("/detect-dbt-projects", methods=["GET"])
def detect_dbt_projects():
    """
    Auto-detect dbt projects by searching for dbt_project.yml files.

    If allowed_projects is defined in config (list of project names),
    only return projects whose name matches the list.

    Returns:
        JSON response with list of detected dbt projects
    """
    import yaml

    # User is expected to run dbt_training_wheels from inside the dbt project they want to work with
    cwd = Path.cwd()
    dbt_project_yml = cwd / "dbt_project.yml"

    detected_projects = []

    if dbt_project_yml.exists():
        # Read project name from dbt_project.yml
        try:
            with open(dbt_project_yml) as f:
                dbt_config = yaml.safe_load(f)
                project_name = dbt_config.get("name", cwd.name)
        except (yaml.YAMLError, OSError) as e:
            logger.debug(f"Could not read project name from dbt_project.yml: {e}")
            project_name = cwd.name

        detected_projects.append(
            {"path": str(cwd), "name": project_name, "dbt_project_yml": str(dbt_project_yml), "from_config": True}
        )

    return jsonify({"success": True, "projects": detected_projects, "count": len(detected_projects), "source": "cwd"})
