"""Service for generating dbt model files."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, cast

import yaml

from dbt_training_wheels.models.types import (
    AnalysisResult,
    QueryInput,
)
from dbt_training_wheels.services.naming_resolver import NamingResolver
from dbt_training_wheels.services.template_service import get_template_service
from dbt_training_wheels.storage import FileSystemStorage
from dbt_training_wheels.utils.naming import build_model_name, get_case_style_and_separator, normalize_identifier
from dbt_training_wheels.utils.sql_formatter import format_dbt_model
from dbt_training_wheels.utils.sql_parser import (
    analyze_sql_content,
    detect_sql_type,
    extract_standalone_select,
    transform_sql_with_sources,
)

logger = logging.getLogger(__name__)

# Storage instance for loading cross-project ref decisions
_storage = FileSystemStorage()

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import OrganizationConfig
    from dbt_training_wheels.models.query_configuration import QueryConfiguration


def detect_sql_issues(sql: str, model_name: str) -> list[str]:
    """Detect common SQL issues in generated models.

    Args:
        sql: The SQL content to check
        model_name: Name of the model (for context in error messages)

    Returns:
        List of issue descriptions (empty if no issues)
    """
    import re

    issues = []

    # Issue 1: Missing prefix in ref() calls
    # Refs should be like ref('int__table') not ref('table')
    refs_without_prefix = re.findall(r"\{\{\s*ref\(['\"]([^_'\"]*?)['\"]", sql)
    if refs_without_prefix:
        issues.append(f"Refs missing prefix: {refs_without_prefix}")

    # Issue 2: Backticks inside ref() calls (should be removed)
    if re.search(r"ref\(['\"]`", sql):
        issues.append("Backticks found inside ref() call - should be removed")

    # Issue 3: Self-references (model references itself)
    if f"ref('{model_name}')" in sql or f'ref("{model_name}")' in sql:
        issues.append("Self-reference detected: model references itself")

    # Issue 4: Fully qualified names still present (not converted to source/ref)
    fully_qualified = re.findall(r"`([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)`", sql)
    if fully_qualified:
        issues.append(f"Fully qualified table names not converted: {fully_qualified[:3]}")  # Show first 3

    # Issue 5: Empty ref() calls
    if re.search(r"\{\{\s*ref\(['\"]['\"]", sql):
        issues.append("Empty ref() call found")

    # Issue 6: Duplicate DECLARE statements (common bug)
    declare_count = len(re.findall(r"\bDECLARE\b", sql, re.IGNORECASE))
    if declare_count > 1:
        issues.append(f"Multiple DECLARE statements found ({declare_count}) - should be converted to CTEs or vars")

    return issues


def _scan_sources_via_git_clone(repository: str, scan_path: str) -> set[tuple[str, str]]:
    """
    Scan for existing sources by cloning repo with SSH keys (no GitHub token needed!).

    Args:
        repository: GitHub repo in "owner/repo" format
        scan_path: Path within repo to scan (e.g., "dbt_projects/myproject/models")

    Returns:
        Set of (source_name, table_name) tuples
    """
    import subprocess
    import tempfile
    from pathlib import Path

    # Convert to SSH URL
    ssh_url = f"git@github.com:{repository}.git"
    logger.info(f"[File Generator] Cloning {ssh_url} to scan for existing sources (using SSH keys)")

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # Clone with SSH (uses mounted SSH keys automatically!)
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", ssh_url, tmpdir],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )

            # Scan the path within cloned repo
            full_scan_path = Path(tmpdir) / scan_path
            if not full_scan_path.exists():
                logger.warning(f"[File Generator] Path {scan_path} not found in cloned repo")
                return set()

            # Use existing local scan function
            return scan_existing_sources(tmpdir, scan_path)

        except subprocess.CalledProcessError as e:
            logger.error(f"[File Generator] Failed to clone {ssh_url}: {e.stderr}")
            raise
        except subprocess.TimeoutExpired:
            logger.error(f"[File Generator] Git clone timed out for {ssh_url}")
            raise
        except Exception as e:
            logger.error(f"[File Generator] Failed to scan sources via git clone: {e}")
            raise


def scan_existing_sources(dbt_project_path: str, models_path: str) -> set[tuple[str, str]]:
    """
    Scan existing dbt source files to find already-defined sources and tables.

    Args:
        dbt_project_path: Root path to the dbt project
        models_path: Relative or absolute path to models directory

    Returns:
        Set of (source_name, table_name) tuples that are already defined
    """
    existing_sources: set[tuple[str, str]] = set()

    # Determine absolute models path
    if os.path.isabs(models_path):
        abs_models_path = models_path
    else:
        abs_models_path = os.path.join(dbt_project_path, models_path)

    if not os.path.exists(abs_models_path):
        return existing_sources

    # Find all .yml files in the models directory (recursively)
    for root, _dirs, files in os.walk(abs_models_path):
        for file in files:
            if file.endswith(".yml") or file.endswith(".yaml"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path) as f:
                        content = yaml.safe_load(f)

                    if not content or "sources" not in content:
                        continue

                    # Extract source and table names
                    for source in content["sources"]:
                        source_name = source.get("name")
                        if not source_name:
                            continue

                        tables = source.get("tables", [])
                        for table in tables:
                            table_name = table.get("name")
                            if table_name:
                                existing_sources.add((source_name, table_name))

                except Exception as e:
                    # Log and skip files that can't be parsed
                    logger.debug(f"Skipping unparseable YAML file {file_path}: {e}")
                    continue

    return existing_sources


def scan_public_models(dbt_project_path: str, models_path: str = "models") -> set[str]:
    """
    Scan dbt model YAML files to find models with access: public.

    Only models marked as public can be used with cross-project refs.

    Args:
        dbt_project_path: Root path to the dbt project
        models_path: Relative or absolute path to models directory

    Returns:
        Set of model names that have access: public
    """
    public_models: set[str] = set()

    # Determine absolute models path
    if os.path.isabs(models_path):
        abs_models_path = models_path
    else:
        abs_models_path = os.path.join(dbt_project_path, models_path)

    if not os.path.exists(abs_models_path):
        return public_models

    # Find all .yml files in the models directory (recursively)
    for root, _dirs, files in os.walk(abs_models_path):
        for file in files:
            if file.endswith(".yml") or file.endswith(".yaml"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path) as f:
                        content = yaml.safe_load(f)

                    if not content or "models" not in content:
                        continue

                    # Extract models with access: public
                    for model in content["models"]:
                        model_name = model.get("name")
                        if not model_name:
                            continue

                        # Check for access: public in config block
                        config = model.get("config", {})
                        access = config.get("access", "").lower()

                        if access == "public":
                            public_models.add(model_name)
                            logger.debug(f"Found public model: {model_name}")

                except Exception as e:
                    # Log and skip files that can't be parsed
                    logger.debug(f"Skipping unparseable YAML file {file_path}: {e}")
                    continue

    return public_models


def generate_sources_yml(
    analysis_data: dict[str, Any] | None = None,
    existing_sources: set[tuple[str, str]] | None = None,
    config: OrganizationConfig | None = None,
) -> str:
    """
    Generate a sources.yml file based on analysis data, excluding already-defined sources.

    Args:
        analysis_data: Analysis results containing hardcoded tables
        existing_sources: Set of (source_name, table_name) tuples that already exist
        config: Optional organization config for customization

    Returns:
        YAML content for sources.yml
    """
    if not analysis_data or not analysis_data.get("hardcodedTables"):
        return """version: 2

sources:
  - name: your_dataset
    # Update with your actual dataset name
    tables:
      - name: your_table
        # Update with your actual table name
"""

    if existing_sources is None:
        existing_sources = set()

    # Group tables by dataset
    sources_by_dataset: dict[str, list[str]] = {}
    logger.info(f"[generate_sources_yml] Processing {len(analysis_data.get('hardcodedTables', []))} hardcoded tables")

    for i, table in enumerate(analysis_data["hardcodedTables"]):
        logger.info(
            f"[generate_sources_yml] Table {i}: {table.get('table')} - isSelfRef={table.get('isSelfReference')}, isCrossRef={table.get('isCrossProjectRef')}"
        )

        # Skip self-references (tables created within the same script)
        if table.get("isSelfReference"):
            logger.info("[generate_sources_yml]   -> Skipped (self-reference)")
            continue

        # Skip cross-project refs (already handled via ref('project', 'model'))
        if table.get("isCrossProjectRef"):
            logger.info("[generate_sources_yml]   -> Skipped (cross-project ref)")
            continue

        parts = table["table"].split(".")
        dataset = parts[-2] if len(parts) >= 2 else "default"
        table_name = parts[-1]

        # Skip if this source/table combo already exists
        if (dataset, table_name) in existing_sources:
            logger.info("[generate_sources_yml]   -> Skipped (already in existing sources)")
            continue

        logger.info(f"[generate_sources_yml]   -> Added to sources.yml: {dataset}.{table_name}")
        if dataset not in sources_by_dataset:
            sources_by_dataset[dataset] = []
        if table_name not in sources_by_dataset[dataset]:
            sources_by_dataset[dataset].append(table_name)

    logger.info(f"[generate_sources_yml] Result: {len(sources_by_dataset)} datasets with sources")

    # If all sources were filtered out, return a message
    if not sources_by_dataset:
        return """✓ No new sources needed

All external tables referenced in this query are already defined in your dbt project's source files.

Your existing sources cover:
- All required datasets
- All required tables

You can proceed to the next step without adding any sources.
"""

    # Group tables by dataset with their project/database
    # Structure: {dataset: {'project': project_name, 'tables': [table_names]}}
    sources_with_projects = {}
    for table in analysis_data["hardcodedTables"]:
        # Skip self-references (tables created within the same script)
        if table.get("isSelfReference"):
            continue

        # Skip cross-project refs (already handled via ref('project', 'model'))
        if table.get("isCrossProjectRef"):
            continue

        parts = table["table"].split(".")
        if len(parts) >= 3:
            project = parts[-3]  # e.g., my-gcp-project
            dataset = parts[-2]  # e.g., my_dataset
            table_name = parts[-1]
        elif len(parts) == 2:
            project = None
            dataset = parts[-2]
            table_name = parts[-1]
        else:
            continue

        # Skip if this source/table combo already exists
        if (dataset, table_name) in existing_sources:
            continue

        if dataset not in sources_with_projects:
            sources_with_projects[dataset] = {"project": project, "tables": []}
        if table_name not in sources_with_projects[dataset]["tables"]:
            sources_with_projects[dataset]["tables"].append(table_name)

    if not sources_with_projects:
        return """✓ No new sources needed

All external tables referenced in this query are already defined in your dbt project's source files.
"""

    # Always include dynamic database block when project info is available
    # This matches the original behavior of the Python fallback
    include_dynamic_database = True

    # Try template-based generation first
    template_service = get_template_service(config)
    if template_service.template_exists("sources.yml.j2"):
        rendered = template_service.render_template(
            "sources.yml.j2",
            sources_with_projects=sources_with_projects,
            include_dynamic_database=include_dynamic_database,
        )
        if rendered is not None:
            return rendered

    # Fallback to existing Python-based generation
    yaml_content = "version: 2\n\nsources:"

    for dataset, info in sources_with_projects.items():
        yaml_content += f"\n  - name: {dataset}"

        # Add Jinja database block if we have a project name
        if info["project"]:
            project = info["project"]
            yaml_content += "\n    database: |"
            yaml_content += f'\n      {{%- if target.name == "local" -%}} {project}'
            yaml_content += f'\n      {{%- elif target.name.startswith("dev") -%}} {project}'
            yaml_content += f'\n      {{%- elif target.name.startswith("prd") -%}} {project}'
            yaml_content += "\n      {%- else -%} invalid_environment_profile_target"
            yaml_content += "\n      {%- endif -%}"

        yaml_content += f'\n    description: "Raw data from {dataset}"'
        yaml_content += "\n    tables:"
        for table_name in info["tables"]:
            yaml_content += f"\n      - name: {table_name}"

    yaml_content += "\n"
    return yaml_content


def generate_dbt_project_domain_block(
    domain_area: str,
    layers: list[str],
    naming_prefix: str = "dbt_training_wheels",
) -> str:
    """Generate a dbt_project.yml domain block for a new domain area.

    Produces a YAML snippet like:
        sales:
          +tags: "dbt_training_wheels_sales"
          +dataset: |
              {%- if target.name == "local" -%} {{ target.dataset }}
              {%- else -%} sales
              {%- endif -%}
          staging:
            +tags: "dbt_stg_sales"
          mart:
            +tags: "dbt_mart_sales"

    Args:
        domain_area: The domain name (e.g., "sales")
        layers: List of layer names present (e.g., ["staging", "intermediate", "mart"])
        naming_prefix: Tag prefix for the domain (default: "dbt_training_wheels")

    Returns:
        YAML snippet string (indented with 4 spaces for insertion under the project key)
    """
    indent = "    "
    block = f"\n{indent}{domain_area}:\n"
    block += f'{indent}  +tags: "{naming_prefix}_{domain_area}"\n'
    block += f"{indent}  +dataset: |\n"
    block += f'{indent}      {{%- if target.name == "local" -%}} {{{{ target.dataset }}}}\n'
    block += f"{indent}      {{%- else -%}} {domain_area}\n"
    block += f"{indent}      {{%- endif -%}}\n"

    # Layer tag mapping
    layer_tag_prefixes = {
        "staging": "dbt_stg",
        "intermediate": "dbt_int",
        "mart": "dbt_mart",
    }

    for layer in layers:
        tag_prefix = layer_tag_prefixes.get(layer)
        if tag_prefix:
            block += f"{indent}  {layer}:\n"
            block += f'{indent}    +tags: "{tag_prefix}_{domain_area}"\n'

    return block


def generate_layer_schema_yml(
    models: list[dict[str, Any]],
    model_configs: dict[str, dict[str, Any]],
    prefix: str = "",
    case_style: str = "snake_case",
    separator: str = "_",
) -> str:
    """Generate schema.yml for models in any layer with doc() references.

    Args:
        models: List of model dicts with names
        model_configs: Model configurations including descriptions
        prefix: Model name prefix (e.g., "mart__", "int__", "stg__")
        case_style: Case style for name normalization
        separator: Separator for name normalization

    Returns:
        YAML content as string with doc() references
    """
    content = "version: 2\n\nmodels:\n"

    for model in models:
        model_name = build_model_name(
            model["name"], prefix=prefix, suffix="", case_style=case_style, separator=separator
        )

        content += f"  - name: {model_name}\n"
        content += f"    description: '{{{{ doc(\"{model_name}\") }}}}'\n\n"

    return content


# Keep backward-compatible alias
generate_mart_schema_yml = generate_layer_schema_yml


def generate_layer_docs_md(
    models: list[dict[str, Any]],
    model_configs: dict[str, dict[str, Any]],
    prefix: str = "",
    query_name: str = "",
    layer_type: str = "mart",
    case_style: str = "snake_case",
    separator: str = "_",
) -> str:
    """Generate docs.md with doc blocks for models in any layer.

    Args:
        models: List of model dicts
        model_configs: Model configurations with descriptions
        prefix: Model name prefix (e.g., "mart__", "int__", "stg__")
        query_name: Original query name for context
        layer_type: Layer type for default description generation
        case_style: Case style for name normalization
        separator: Separator for name normalization

    Returns:
        Markdown content with {% docs %} blocks
    """
    content = ""

    for model in models:
        model_name = build_model_name(
            model["name"], prefix=prefix, suffix="", case_style=case_style, separator=separator
        )
        # Look up config by normalized name first, then unprefixed (descriptions may be
        # stored under either key depending on how the UI built the model name)
        config = model_configs.get(model_name) or model_configs.get(model["name"]) or {}

        # Get description from config or use default
        description = config.get("description")
        if not description:
            description = _generate_default_description(model["name"], query_name, layer_type)

        content += f"{{% docs {model_name} %}}\n"
        content += f"{description}\n"
        content += "{% enddocs %}\n\n"

    return content


# Keep backward-compatible alias
generate_marts_docs_md = generate_layer_docs_md


def _generate_default_description(model_name: str, query_name: str, layer_type: str = "mart") -> str:
    """Generate default description if user doesn't provide one.

    Args:
        model_name: Model name without prefix
        query_name: Original query name
        layer_type: Layer type ('staging', 'intermediate', 'mart')

    Returns:
        Default markdown description
    """
    layer_labels = {
        "staging": "Staging model",
        "intermediate": "Intermediate model",
        "mart": "Mart model",
    }
    label = layer_labels.get(layer_type, f"{layer_type.title()} model")
    description = f"{label}: {model_name}\n\n"

    if query_name:
        description += f"Generated from query: {query_name}\n\n"

    layer_hints = {
        "staging": """**TODO**: Add detailed description including:
- Source tables being combined
- Key columns and their meaning
- Any cleaning or renaming applied
- Data freshness and update frequency""",
        "intermediate": """**TODO**: Add detailed description including:
- Transformations and business logic applied
- Upstream models being joined
- Grain of the output (what each row represents)
- Key computed fields or aggregations""",
        "mart": """**TODO**: Add detailed description including:
- Business purpose and key use cases
- Important metrics and dimensions
- Update frequency and dependencies
- Data quality notes and caveats""",
    }

    description += layer_hints.get(layer_type, layer_hints["mart"])

    return description


# Keep backward-compatible alias
_generate_default_mart_description = _generate_default_description


def generate_intermediate_model_content(
    model_name: str,
    sql_logic: str,
    config: OrganizationConfig | None = None,
    model_config: dict[str, Any] | None = None,
    layer_name: str = "intermediate",
    conversion_tag: str | None = None,
) -> str:
    """Generate content for an intermediate or staging model file.

    Args:
        model_name: Name of the model
        sql_logic: The SQL logic (may already include TODO comments)
        config: Organization config
        model_config: Model-specific configuration
        layer_name: The layer type ('staging' or 'intermediate')
        conversion_tag: Tag identifying the conversion, used by the generated DAG to
            select exactly this conversion's models
    """
    materialization = "table"
    schema = None
    tags = []
    include_config_block = True

    if model_config:
        materialization = model_config.get("materialization", "table")
        schema = model_config.get("schema")
        tags = model_config.get("tags", [])
    elif config and config.output:
        materialization = config.output.default_materialization
        include_config_block = config.output.include_config_block

    # Apply default tags if no model-specific tags are set
    if not tags and config and config.tags and config.tags.default_tags:
        tags = config.tags.default_tags

    # Appended, never substituted: the DAG selects on this tag, so a model that carries
    # its own tags or the org defaults still has to be reachable
    if conversion_tag and conversion_tag not in tags:
        tags = [*tags, conversion_tag]

    config_parts = [f"materialized='{materialization}'"]
    if schema:
        config_parts.append(f"schema='{schema}'")
    if tags:
        tags_str = ", ".join([f"'{t}'" for t in tags])
        config_parts.append(f"tags=[{tags_str}]")

    config_block = f"{{{{ config({', '.join(config_parts)}) }}}}\n\n" if include_config_block else ""

    formatted_sql = format_dbt_model(
        sql_logic, dialect=config.database.dialect if config and config.database else "bigquery"
    )

    # Generate layer-appropriate comments
    if layer_name == "staging":
        layer_comment = f"-- Staging model: {model_name}\n-- Direct source access layer\n"
    else:
        layer_comment = f"-- Intermediate model: {model_name}\n-- Transformations built on staging models\n"

    return f"""{config_block}{layer_comment}
{formatted_sql}"""


def generate_final_model_content(
    table_name: str,
    query_name: str,
    sql_logic: str | None = None,
    config: OrganizationConfig | None = None,
    model_config: dict[str, Any] | None = None,
    conversion_tag: str | None = None,
) -> str:
    """
    Generate content for a final model file.

    Args:
        table_name: Name of the table
        query_name: Name of the source query
        sql_logic: Optional transformed SQL logic to include
        config: Optional OrganizationConfig for customizing output
        model_config: Optional per-model configuration (materialization, schema, tags)
        conversion_tag: Tag identifying the conversion, used by the generated DAG to
            select exactly this conversion's models

    Returns:
        String content for the final model
    """
    # Priority: model_config > config > default
    materialization = "table"
    schema = None
    tags = []
    include_config_block = True
    include_source_comment = True

    if model_config:
        materialization = model_config.get("materialization", "table")
        schema = model_config.get("schema")
        tags = model_config.get("tags", [])
    elif config and config.output:
        materialization = config.output.default_materialization

    if config and config.output:
        include_config_block = config.output.include_config_block
        include_source_comment = config.output.include_source_comment

    # Apply default tags if no model-specific tags are set
    if not tags and config and config.tags and config.tags.default_tags:
        tags = config.tags.default_tags

    # Appended, never substituted: the DAG selects on this tag, so a model that carries
    # its own tags or the org defaults still has to be reachable
    if conversion_tag and conversion_tag not in tags:
        tags = [*tags, conversion_tag]

    # Format SQL if provided
    formatted_sql = None
    if sql_logic:
        dialect = "bigquery"
        if config and config.database:
            dialect = config.database.dialect
        # Use format_dbt_model to preserve TODO/INFORMATIONAL NOTE comments at the top
        formatted_sql = format_dbt_model(sql_logic, dialect=dialect)

    # Try template-based generation first
    template_service = get_template_service(config)
    if template_service.template_exists("final_model.sql.j2"):
        rendered = template_service.render_template(
            "final_model.sql.j2",
            table_name=table_name,
            query_name=query_name,
            sql_logic=formatted_sql,
            materialization=materialization,
            schema=schema,
            tags=tags,
            include_config_block=include_config_block,
            include_source_comment=include_source_comment,
        )
        if rendered is not None:
            return rendered

    # Fallback to existing Python-based generation
    config_parts = [f"materialized='{materialization}'"]
    if schema:
        config_parts.append(f"schema='{schema}'")
    if tags:
        tags_str = ", ".join([f"'{t}'" for t in tags])
        config_parts.append(f"tags=[{tags_str}]")

    config_block = f"{{{{ config({', '.join(config_parts)}) }}}}\n\n" if include_config_block else ""
    source_comment = f"-- Source: {query_name}\n" if include_source_comment else ""

    if formatted_sql:
        return f"""{config_block}-- Final model: {table_name}
{source_comment}-- Generated by DBT Training Wheels Conversion Tool

{formatted_sql}"""
    else:
        return f"""{config_block}-- Final model: {table_name}
{source_comment}
-- NOTE: SQL extraction not available for this table
-- Please copy the transformation logic from your original query

SELECT
  *
FROM {{{{ source('schema', 'table') }}}}"""


def _load_cross_project_decisions(query_id: int) -> dict[str, dict]:
    """Load cross-project ref decisions from storage.

    Args:
        query_id: Query identifier

    Returns:
        Dict mapping original_reference to decision dict
    """
    import json

    filename = f"cross_project_refs_{query_id}.json"
    content = _storage.read_temp_file(filename)

    if not content:
        return {}

    try:
        decisions = json.loads(content)
        # Convert list to lookup dict
        return {d["original_reference"]: d for d in decisions if d.get("original_reference")}
    except (json.JSONDecodeError, KeyError):
        return {}


def generate_files_for_query(
    query: QueryInput,
    analysis_data: AnalysisResult | None = None,
    config: OrganizationConfig | None = None,
    model_configs: list[dict[str, Any]] | None = None,
    project_name: str | None = None,
    query_config: QueryConfiguration | None = None,
    domain_area: str | None = None,
    model_group: str | None = None,
    user_mart_selection: list[str] | None = None,
) -> list[dict[str, str]]:
    """
    Generate all dbt model files for a given query.

    Args:
        query: Query input containing SQL and metadata
        analysis_data: Optional analysis results for enhanced file generation
        config: Optional OrganizationConfig for customizing output
        model_configs: Optional list of per-model configurations (deprecated, use query_config)
        project_name: Optional project name for project-specific GitHub config (base_path)
        query_config: Optional QueryConfiguration with pre-computed naming and decisions.
                     If provided, naming is used directly instead of recomputing.
        domain_area: Optional domain/business area subdirectory (e.g., 'marketing', 'finance')
        model_group: Unique name for the modelto be identified within a domain
        user_mart_selection: Optional list of table names selected for mart layer.
                           If provided, only these tables will get mart models.
                           If None, all tables get mart models (backward compatibility).

    Returns:
        List of generated files with path, content, and model information
    """
    # Import here to avoid circular import

    files = []

    # Log user mart selection for debugging
    if user_mart_selection is not None:
        logger.info(f"[File Generator] User mart selection: {len(user_mart_selection)} tables selected")
        logger.debug(f"[File Generator] Selected tables: {user_mart_selection}")
    else:
        logger.warning("[File Generator] No user_mart_selection - will create mart for ALL tables")

    # Create lookup map: table_name -> config
    model_config_map: dict[str, dict[str, Any]] = {}
    if query_config and query_config.model_configurations:
        # Use QueryConfiguration model configs (preferred)
        model_config_map = query_config.get_model_config_map()
        logger.info(f"[File Generator] Using QueryConfiguration model configs: {len(model_config_map)} models")
    elif model_configs:
        # Fall back to legacy model_configs parameter
        for mc in model_configs:
            table_key = mc.get("table")
            if table_key:
                model_config_map[str(table_key)] = mc

    # Load cross-project ref decisions
    query_id = query.get("id")
    if query_config and query_config.cross_project_decisions:
        # Use QueryConfiguration cross-project decisions (preferred)
        cross_project_decisions = query_config.get_cross_project_decisions_map()
        logger.info(
            f"[File Generator] Using QueryConfiguration cross-project decisions: {len(cross_project_decisions)}"
        )
    else:
        # Fall back to legacy temp file loading
        cross_project_decisions = (
            _load_cross_project_decisions(int(query_id) if isinstance(query_id, int | str) else 0) if query_id else {}
        )
        logger.info(f"[File Generator] Loaded {len(cross_project_decisions)} cross-project decisions from temp file")

    logger.info(f"[File Generator] Decision keys: {list(cross_project_decisions.keys())}")

    # Get naming config - use QueryConfiguration if provided, otherwise use NamingResolver
    if query_config and query_config.naming:
        # Use QueryConfiguration naming (preferred - already computed)
        naming = query_config.naming
        staging_model_prefix = naming.staging_model_prefix
        intermediate_model_prefix = naming.intermediate_model_prefix
        mart_model_prefix = naming.mart_model_prefix
        final_model_suffix = naming.final_model_suffix
        # Use property getters - layer_folder_names is single source of truth
        staging_folder = naming.staging_folder
        intermediate_folder = naming.intermediate_folder
        marts_folder = naming.marts_folder
        sources_file_name = naming.sources_file_name
        logger.info(
            f"[File Generator] Using QueryConfiguration naming: intermediate_prefix={intermediate_model_prefix}"
        )
    elif config:
        # Use NamingResolver for centralized naming logic
        logger.info("[File Generator] Resolving naming using NamingResolver")
        naming_resolver = NamingResolver(config)
        resolved_naming = naming_resolver.resolve(
            query_naming=None,  # No query-specific overrides in this path
            project_name=project_name,
        )

        staging_model_prefix = resolved_naming.staging_model_prefix
        intermediate_model_prefix = resolved_naming.intermediate_model_prefix
        mart_model_prefix = resolved_naming.mart_model_prefix
        staging_folder = resolved_naming.staging_folder
        intermediate_folder = resolved_naming.intermediate_folder
        marts_folder = resolved_naming.marts_folder

        # Set defaults for fields not in ResolvedNaming
        final_model_suffix = ""
        sources_file_name = "sources.yml"
    else:
        # Fallback: Use schema defaults if no config available
        logger.warning("[File Generator] No config available, using schema defaults")
        from dbt_training_wheels.config_schema import ModelNamingConfig

        schema_defaults = ModelNamingConfig()
        intermediate_model_prefix = schema_defaults.intermediate_model_prefix
        mart_model_prefix = schema_defaults.mart_model_prefix
        staging_model_prefix = getattr(schema_defaults, "staging_model_prefix", "stg__")
        final_model_suffix = ""
        # Use property getters - layer_folder_names is single source of truth
        staging_folder = schema_defaults.staging_folder
        intermediate_folder = schema_defaults.intermediate_folder
        marts_folder = schema_defaults.marts_folder
        sources_file_name = "sources.yml"

    # `naming` is only bound when the query config carried one, so read it back off the
    # config rather than assuming the branch above ran
    case_style, separator = get_case_style_and_separator(
        config, project_name, query_config.naming if query_config else None
    )

    # Replace {project} placeholder in prefixes with actual project name if provided
    if project_name:
        if "{project}" in mart_model_prefix:
            mart_model_prefix = mart_model_prefix.replace("{project}", project_name)
        if "{project}" in intermediate_model_prefix:
            intermediate_model_prefix = intermediate_model_prefix.replace("{project}", project_name)
        if "{project}" in staging_model_prefix:
            staging_model_prefix = staging_model_prefix.replace("{project}", project_name)

    # Note: domain_area is used for folder structure only, not for model prefixes
    # Model prefixes come from project-level config

    # Get base_path from project config if available
    base_path = ""
    if project_name and config:
        from dbt_training_wheels.config import get_project_config

        project_config = get_project_config(project_name)
        if project_config and project_config.get("github"):
            base_path = project_config["github"].get("base_path", "")
            if base_path:
                logger.info(f"[File Generator] Using base_path '{base_path}' from project '{project_name}'")

    # Construct path prefix with base_path and domain_area
    # Start with base_path if the dbt project is nested in a repo subdirectory
    models_root = f"{base_path}/models" if base_path else "models"
    path_prefix = models_root

    # Models are written to models/<domain>/, and nothing else:
    #
    #   churn/customer/*.sql  ->  models/customer/
    #   sales/*.sql           ->  models/sales/
    #
    # The conversion is deliberately not a path segment. It's one-off - it names the
    # branch and the pull request, where it's actually useful - while the domain is
    # long-lived and is the only thing a dbt repo should be organised by. Two
    # conversions into the same domain land side by side, which is the intent.
    from dbt_training_wheels.services.domain_resolver import domain_from_filename
    from dbt_training_wheels.services.query_service import conversion_name_for, conversion_tag_for

    query_filename = query.get("filename")  # type: ignore[typeddict-item]
    own_domain = domain_from_filename(query_filename)
    conversion_name = conversion_name_for(query_filename)

    # The docs file still carries the conversion name, so that two conversions sharing a
    # domain folder don't write to the same .md
    docs_name = conversion_name or own_domain or model_group

    # Stamped on every generated model so the DAG can select this conversion alone -
    # the path can't, now that it stops at the domain
    conversion_tag = conversion_tag_for(query_filename)
    domain_of_model: dict[str, str] = {}
    domains_in_order: list[str] = []

    if analysis_data:
        from dbt_training_wheels.services.domain_resolver import attribute_models_to_domains

        for group in attribute_models_to_domains(
            dict(analysis_data),
            config,
            project_name=project_name,
            fallback_domain=domain_area or "",
            query_filename=query.get("filename"),  # type: ignore[typeddict-item]
        ):
            domains_in_order.append(group.domain)
            for attributed in group.models:
                domain_of_model[attributed.model] = group.domain

    def _prefix_for(model_domain: str | None) -> str:
        """Where a model belonging to this domain is written."""
        if not model_domain:
            return path_prefix
        return f"{models_root}/{model_domain}"

    def _prefix_for_model(model_base_name: str) -> str:
        return _prefix_for(domain_of_model.get(model_base_name))

    logger.info(f"[File Generator] Final path_prefix: {path_prefix}")
    if domains_in_order:
        logger.info(f"[File Generator] Writing {len(domains_in_order)} domain path(s): {domains_in_order}")

    if config and config.sources:
        sources_file_name = config.sources.sources_file_name

    prep_models, _ = analyze_sql_content(query["sql"], config, project_name=project_name)

    # Extract DECLARE variables from full SQL once for use in all generated models
    from dbt_training_wheels.utils.sql_parser import extract_declare_variables

    full_sql_declare_variables = extract_declare_variables(query["sql"])

    # Get hardcoded tables from analysis for SQL transformation
    hardcoded_tables = []
    if analysis_data and analysis_data.get("hardcodedTables"):
        hardcoded_tables = analysis_data["hardcodedTables"]

    # Update hardcodedTables with cross-project ref decisions
    # This ensures that tables using cross-project refs are marked and excluded from sources.yml
    logger.info(
        f"[File Generator] Processing {len(hardcoded_tables)} hardcoded tables with {len(cross_project_decisions)} cross-project decisions"
    )
    if cross_project_decisions and hardcoded_tables:
        for table in hardcoded_tables:
            full_table_ref = str(table.get("table", ""))
            # Build lookup key (dataset.table format)
            parts = full_table_ref.replace("`", "").replace('"', "").split(".")
            if len(parts) >= 2:
                lookup_key = f"{parts[-2]}.{parts[-1]}"
            else:
                lookup_key = parts[-1] if parts else ""

            # Check if this table has a cross-project ref decision
            decision = cross_project_decisions.get(lookup_key)
            if decision and decision.get("use_cross_ref"):
                project = decision.get("project", "")
                model = decision.get("model", "")
                if project and model:
                    # Mark as cross-project ref so it's excluded from sources.yml
                    table["isCrossProjectRef"] = True  # type: ignore[typeddict-unknown-key]
                    table["crossProjectProject"] = project  # type: ignore[typeddict-unknown-key]
                    table["crossProjectModel"] = model  # type: ignore[typeddict-unknown-key]
                    logger.info(f"[File Generator] Marked {lookup_key} as cross-project ref to {project}.{model}")
            else:
                table["isCrossProjectRef"] = False  # type: ignore[typeddict-unknown-key]
                table.pop("crossProjectProject", None)  # type: ignore[typeddict-item]
                table.pop("crossProjectModel", None)  # type: ignore[typeddict-item]

    # Log final state for debugging
    cross_ref_count = sum(1 for t in hardcoded_tables if t.get("isCrossProjectRef"))
    self_ref_count = sum(1 for t in hardcoded_tables if t.get("isSelfReference"))
    logger.info(
        f"[File Generator] Table breakdown: {len(hardcoded_tables)} total, {cross_ref_count} cross-project, {self_ref_count} self-reference, {len(hardcoded_tables) - cross_ref_count - self_ref_count} sources"
    )

    # Scan existing sources - from GitHub if enabled, otherwise from local dbt project
    existing_sources = set()

    # For WHERE to SCAN for existing sources - always use "models" root to find all sources
    # (Sources can be defined anywhere in the models directory)
    models_path_for_scanning = "models"
    logger.info(f"[File Generator] Scanning for existing sources at: '{models_path_for_scanning}'")

    # Check if GitHub is enabled (check defaults first, same as preview endpoint)
    # IMPORTANT: Use project-specific base_path for correct scan path
    github_enabled = False
    defaults_github: Any = None
    if config:
        if (
            config.defaults
            and config.defaults.dbt_config
            and config.defaults.dbt_config.github
            and config.defaults.dbt_config.github.enabled
        ):
            defaults_github = config.defaults.dbt_config.github
            github_enabled = True
            logger.info("[File Generator] Using config.defaults.dbt_config.github for source scanning")
        elif config.github and config.github.enabled:
            defaults_github = config.github
            github_enabled = True
            logger.info("[File Generator] Using config.github for source scanning")
        else:
            logger.warning(
                f"[File Generator] No GitHub config found - config.github={config.github is not None if config else 'No config'}, config.defaults={config.defaults is not None if config else 'No config'}"
            )

    if github_enabled and defaults_github:
        # Try to scan for existing sources
        from dbt_training_wheels.config import get_project_config

        # Get project-specific base_path (same logic as preview endpoint in models.py)
        base_path = ""
        if project_name:
            project_config = get_project_config(project_name)
            if project_config and project_config.get("github"):
                base_path = project_config["github"].get("base_path", "")
                logger.info(f"[File Generator] Got base_path '{base_path}' from project '{project_name}'")

        repository = defaults_github.repository
        scan_path = f"{base_path}/{models_path_for_scanning}" if base_path else models_path_for_scanning

        # Scan via SSH clone (uses mounted SSH keys - no token needed!)
        if repository:
            try:
                logger.info("[File Generator] → Scanning for existing sources via SSH")
                existing_sources = _scan_sources_via_git_clone(repository, scan_path)
                logger.info(f"[File Generator] ✓ Found {len(existing_sources)} existing sources")
            except Exception as e:
                logger.warning(f"[File Generator] ✗ SSH clone failed for source scanning: {e}")
    else:
        # Use local scanning from current working directory
        project_path = os.getcwd()
        if os.path.exists(os.path.join(project_path, "dbt_project.yml")):
            try:
                existing_sources = scan_existing_sources(project_path, models_path_for_scanning)
            except Exception:
                # If scanning fails, continue without filtering
                pass

    logger.info(f"[File Generator] Final existing_sources count: {len(existing_sources)}")
    if len(existing_sources) > 0:
        logger.info(f"[File Generator] Sample existing sources: {list(existing_sources)[:5]}")

    # Verify flags before generating sources.yml
    for i, table in enumerate(hardcoded_tables[:3]):  # Log first 3 for debugging
        logger.info(
            f"[File Generator] Table {i}: {table.get('table')} - isCrossProjectRef={table.get('isCrossProjectRef')}, isSelfReference={table.get('isSelfReference')}"
        )

    # Generate sources.yml only if there are new sources to add
    sources_content = generate_sources_yml(analysis_data, existing_sources, config)  # type: ignore[arg-type]
    # Skip if all sources already exist (content starts with "✓")
    logger.info(
        f"[File Generator] sources_content starts with: '{sources_content[:50] if sources_content else 'None'}'"
    )
    logger.info(f"[File Generator] Does it start with ✓? {sources_content.startswith('✓')}")
    if not sources_content.startswith("✓"):
        # Sources are shared across every domain in the conversion, so the file goes at
        # the models root. It used to be written inside the first domain's folder, which
        # meant models/sample2/... referenced sources declared under models/sample1/ -
        # a file every domain needs, living in one arbitrary domain's directory.
        #
        # It still ships in the first domain's branch, which is the bottom of the stack,
        # so every later branch inherits it before its own models are parsed.
        sources_path = f"{models_root}/{sources_file_name}" if models_root else sources_file_name
        files.append(
            {
                "path": sources_path,
                "type": "config",
                "content": sources_content,
                "domain": domains_in_order[0] if domains_in_order else "",
            }
        )
        logger.info(f"[File Generator] ✅ Added sources.yml at: {sources_path}")
    else:
        logger.info("[File Generator] ✅ Skipped sources.yml - no new sources needed")

    # Detect SQL type to handle different patterns
    sql_type = detect_sql_type(query["sql"])

    # Generate prep + final models for ALL tables created by the script
    final_tables = query["tables"] if query.get("tables") else []  # type: ignore[typeddict-item]

    # If analysis_data present, prefer its finalTableSqls (includes transformedSql)
    if analysis_data and analysis_data.get("finalTableSqls"):
        final_tables = [t.get("table") for t in analysis_data.get("finalTableSqls", []) if t.get("table")]

    # ------------------------------------------------------------------
    # If analysis_data present, generate staging/intermediate from layerClassification
    # ------------------------------------------------------------------
    if analysis_data and analysis_data.get("layerClassification"):
        layer_classification = analysis_data.get("layerClassification", {})

        # Helper to normalize naming for build_model_name
        case_style, separator = get_case_style_and_separator(config, project_name)

        added_paths = set()

        def _add_model(layer_name: str, comp: dict[str, Any], prefix: str, folder: str):
            model_base = comp.get("name")
            if not model_base:
                return
            model_name = build_model_name(
                model_base,
                prefix=prefix,
                suffix="",
                case_style=case_style,
                separator=separator,
            )
            file_name = f"{model_name}.sql"
            path = f"{_prefix_for_model(model_base)}/{folder}/{file_name}"
            if path in added_paths:
                return
            sql_content = comp.get("transformedSql") or comp.get("sql") or ""
            logger.info(
                f"[File Generator] Creating {layer_name} model: {model_name}.sql "
                f"(SQL: {len(sql_content)} chars, has transformedSql: {comp.get('transformedSql') is not None})"
            )

            # Generate content
            content = generate_intermediate_model_content(
                model_name,
                sql_content,
                config,
                model_config_map.get(model_name),
                layer_name=layer_name,  # Pass layer name for correct comments
                conversion_tag=conversion_tag,
            )

            # Validate generated SQL for common issues
            issues = detect_sql_issues(content, model_name)
            if issues:
                logger.warning(f"[File Generator] ⚠️  Issues detected in {model_name}:")
                for issue in issues:
                    logger.warning(f"  - {issue}")

            files.append(
                {
                    "path": path,
                    "type": "model",
                    "content": content,
                    "domain": domain_of_model.get(model_base, ""),
                }
            )
            added_paths.add(path)

        # Staging models: CTEs with multiple external sources
        for comp in layer_classification.get("staging", []):
            _add_model("staging", comp, staging_model_prefix, staging_folder)

        # Intermediate models: CTEs or components
        for comp in layer_classification.get("intermediate", []):
            _add_model("intermediate", comp, intermediate_model_prefix, intermediate_folder)

        # Mart models: still generated below using final_tables (filtered by user_mart_selection)

    # ------------------------------------------------------------------

    if final_tables:
        # Standard case: we have explicit table names from CREATE/INSERT
        for table in final_tables:
            # Ensure table is a string
            table_str = str(table) if not isinstance(table, str) else table
            normalized_table = normalize_identifier(table_str, case_style=case_style, separator=separator)

            # If user_mart_selection is provided, skip non-selected
            if (
                user_mart_selection is not None
                and table_str not in user_mart_selection
                and normalized_table not in user_mart_selection
            ):
                logger.debug(f"[File Generator] Skipping final model for '{table_str}' (not selected by user)")
                continue

            # Get model-specific config for final model
            model_config = model_config_map.get(
                build_model_name(
                    normalized_table,
                    prefix=mart_model_prefix,
                    suffix=final_model_suffix,
                    case_style=case_style,
                    separator=separator,
                )
            ) or model_config_map.get(table_str)
            # Get model-specific config for intermediate model (may be stored with int__ prefix)
            intermediate_model_config = model_config_map.get(
                build_model_name(
                    normalized_table,
                    prefix=intermediate_model_prefix,
                    suffix="",
                    case_style=case_style,
                    separator=separator,
                )
            ) or model_config_map.get(table_str)

            # Get both transformed and original SQL from analysis_data if available
            # Also get the pre-computed upstreamCte from analysis phase
            transformed_sql = None
            precomputed_upstream_cte = None
            if analysis_data and analysis_data.get("finalTableSqls"):
                for item in analysis_data["finalTableSqls"]:
                    if item.get("table") == table_str or item.get("table") == normalized_table:
                        transformed_sql = item.get("transformedSql") or item.get("sql")
                        precomputed_upstream_cte = item.get("upstreamCte")  # Pre-computed in analysis phase
                        logger.debug(
                            f"[File Generator] Found precomputed upstreamCte for '{table_str}': {precomputed_upstream_cte}"
                        )
                        break

            # Also check layer_classification.mart for the upstreamCte (with unique naming applied)
            if layer_classification and layer_classification.get("mart"):
                mart_layer = layer_classification["mart"]
                for mart_comp in mart_layer:
                    if mart_comp.get("name") == table_str or mart_comp.get("name") == normalized_table:
                        precomputed_upstream_cte = mart_comp.get("upstreamCte")
                        logger.info(
                            f"[File Generator] Using upstreamCte from layer_classification for '{table_str}': {precomputed_upstream_cte}"
                        )
                        break

            # In the new 3-layer architecture, CTEs stay inline within their parent table
            # The mart model should ALWAYS reference the intermediate model with the SAME BASE NAME
            # (not try to detect CTEs, which were internal to the INSERT statement)
            if layer_classification:
                # Check if this table has a corresponding intermediate layer model
                # Look through intermediate layer for a model with matching name
                intermediate_layer = layer_classification.get("intermediate", [])
                found_matching_intermediate = False

                for int_component in intermediate_layer:
                    int_name = int_component.get("name", "")
                    # Check if intermediate model name matches this table
                    if int_name == table_str or int_name == normalized_table:
                        found_matching_intermediate = True
                        logger.info(
                            f"[File Generator] Found matching intermediate model '{int_name}' for mart '{table_str}'"
                        )
                        break

                if found_matching_intermediate:
                    # Use the table name to build the intermediate model reference
                    # This ensures mart__X references int__X (same base name)
                    intermediate_model_name = build_model_name(
                        normalized_table,
                        prefix=intermediate_model_prefix,
                        suffix="",
                        case_style=case_style,
                        separator=separator,
                    )
                    logger.info(
                        f"[File Generator] Mart '{table}' will ref '{intermediate_model_name}' (same base name pattern)"
                    )
                else:
                    # No matching intermediate found - check if there's a staging model instead
                    staging_layer = layer_classification.get("staging", [])
                    found_matching_staging = False

                    for stg_component in staging_layer:
                        stg_name = stg_component.get("name", "")
                        if stg_name == table_str or stg_name == normalized_table:
                            found_matching_staging = True
                            logger.info(
                                f"[File Generator] Found matching staging model '{stg_name}' for mart '{table_str}'"
                            )
                            break

                    if found_matching_staging:
                        # Reference the staging model
                        intermediate_model_name = build_model_name(
                            normalized_table,
                            prefix=staging_model_prefix,
                            suffix="",
                            case_style=case_style,
                            separator=separator,
                        )
                        logger.info(
                            f"[File Generator] Mart '{table}' will ref staging model '{intermediate_model_name}'"
                        )
                    else:
                        # Fallback: assume intermediate with same name
                        intermediate_model_name = build_model_name(
                            normalized_table,
                            prefix=intermediate_model_prefix,
                            suffix="",
                            case_style=case_style,
                            separator=separator,
                        )
                        logger.warning(
                            f"[File Generator] No matching int/stg model for '{table_str}', "
                            f"defaulting to '{intermediate_model_name}'"
                        )

            # MART MODEL: Always reference the detected/computed intermediate model
            mart_sql = f"SELECT * FROM {{{{ ref('{intermediate_model_name}') }}}}"
            logger.info(f"[File Generator] Creating mart model for '{table_str}' → refs '{intermediate_model_name}'")
            content = generate_final_model_content(
                normalized_table, query["name"], mart_sql, config, model_config, conversion_tag=conversion_tag
            )

            # Use schema from model_config for folder placement
            folder = marts_folder
            if model_config and model_config.get("schema"):
                folder = model_config["schema"]

            # Build path with configurable folder, prefix and suffix
            mart_model_name = build_model_name(
                normalized_table,
                prefix=mart_model_prefix,
                suffix=final_model_suffix,
                case_style=case_style,
                separator=separator,
            )
            mart_file_name = f"{mart_model_name}.sql"

            # Validate generated SQL for common issues
            issues = detect_sql_issues(content, mart_model_name)
            if issues:
                logger.warning(f"[File Generator] ⚠️  Issues detected in {mart_model_name}:")
                for issue in issues:
                    logger.warning(f"  - {issue}")

            files.append(
                {
                    "path": f"{_prefix_for_model(normalized_table)}/{folder}/{mart_model_name}.sql",
                    "type": "model",
                    "content": content,
                    "domain": domain_of_model.get(normalized_table, ""),
                }
            )
    elif sql_type in ("standalone_select", "with_cte"):
        # Handle standalone SELECT or WITH...SELECT queries
        # These don't have explicit table names, so we create a single model
        standalone_sql = extract_standalone_select(query["sql"])
        if standalone_sql:
            # Transform the SQL with source() calls and DECLARE variables
            transformed_sql = transform_sql_with_sources(
                standalone_sql,
                cast(list[dict[str, Any]], hardcoded_tables),
                cross_project_decisions,
                full_sql_declare_variables,
            )
            # Use query name as model name (sanitized)
            model_name = normalize_identifier(query["name"], case_style=case_style, separator=separator)

            # Get model-specific config for final model
            model_config = model_config_map.get(model_name)
            # Get model-specific config for intermediate model (may be stored with int__ prefix)
            intermediate_model_config = model_config_map.get(
                build_model_name(
                    model_name,
                    prefix=intermediate_model_prefix,
                    suffix="",
                    case_style=case_style,
                    separator=separator,
                )
            ) or model_config_map.get(model_name)

            # INTERMEDIATE MODEL: Contains all transformation logic
            intermediate_model_name = build_model_name(
                model_name,
                prefix=intermediate_model_prefix,
                suffix="",
                case_style=case_style,
                separator=separator,
            )
            intermediate_file_name = f"{intermediate_model_name}.sql"
            files.append(
                {
                    "path": f"{path_prefix}/{intermediate_folder}/{intermediate_file_name}",
                    "type": "model",
                    "content": generate_intermediate_model_content(
                        intermediate_model_name,
                        transformed_sql,
                        config,
                        intermediate_model_config,
                        conversion_tag=conversion_tag,
                    ),
                }
            )

            # MART MODEL: Only create if user selected this model (or no selection provided)
            should_create_mart = user_mart_selection is None or model_name in user_mart_selection

            if should_create_mart:
                logger.debug(f"[File Generator] Creating mart model for standalone query '{model_name}'")
                mart_sql = f"SELECT * FROM {{{{ ref('{intermediate_model_name}') }}}}"
                content = generate_final_model_content(
                    model_name, query["name"], mart_sql, config, model_config, conversion_tag=conversion_tag
                )

                # Use schema from model_config for folder placement
                folder = marts_folder
                if model_config and model_config.get("schema"):
                    folder = model_config["schema"]

                # Build path with configurable folder, prefix and suffix
                mart_file_name = f"{mart_model_prefix}{model_name}{final_model_suffix}.sql"
                files.append({"path": f"{path_prefix}/{folder}/{mart_file_name}", "type": "model", "content": content})
            else:
                logger.debug(f"[File Generator] Skipping mart model for '{model_name}' (not in user selection)")

    # ------------------------------------------------------------------
    # Generate documentation files (schema.yml and docs.md) for all layers
    # ------------------------------------------------------------------
    logger.info("[File Generator] Checking for documentation generation...")

    if layer_classification:
        # Define layer config: (layer_key, prefix, folder)
        layer_doc_configs = [
            ("staging", staging_model_prefix, staging_folder),
            ("intermediate", intermediate_model_prefix, intermediate_folder),
            ("mart", mart_model_prefix, marts_folder),
        ]

        # Collect all doc blocks into a single docs file
        # schema.yml and the docs file describe the models beside them, so they're
        # written per domain - a shared one would reference models living elsewhere
        docs_by_domain: dict[str, str] = {}

        for layer_key, layer_prefix, layer_folder in layer_doc_configs:
            layer_components = cast(list[dict[str, Any]], layer_classification.get(layer_key, []))
            if not layer_components:
                continue

            logger.info(f"[File Generator] Generating documentation for {len(layer_components)} {layer_key} models")

            by_domain: dict[str, list[dict[str, Any]]] = {}
            for component in layer_components:
                by_domain.setdefault(domain_of_model.get(component.get("name", ""), ""), []).append(component)

            for model_domain, components in by_domain.items():
                domain_prefix = _prefix_for(model_domain)

                schema_yml = generate_layer_schema_yml(
                    models=components,
                    model_configs=model_config_map,
                    prefix=layer_prefix,
                    case_style=case_style,
                    separator=separator,
                )

                files.append(
                    {
                        "path": f"{domain_prefix}/{layer_folder}/schema.yml",
                        "type": "config",
                        "content": schema_yml,
                        "domain": model_domain,
                    }
                )
                logger.info(f"[File Generator] ✅ Generated {domain_prefix}/{layer_folder}/schema.yml")

                docs_by_domain[model_domain] = docs_by_domain.get(model_domain, "") + generate_layer_docs_md(
                    models=components,
                    model_configs=model_config_map,
                    prefix=layer_prefix,
                    query_name=query.get("name", ""),
                    layer_type=layer_key,
                    case_style=case_style,
                    separator=separator,
                )

        # One docs file per domain, named after the model group
        for model_domain, docs_content in docs_by_domain.items():
            if not docs_content:
                continue
            if not docs_name:
                raise ValueError("Could not determine a documentation file name")

            domain_prefix = _prefix_for(model_domain)
            files.append(
                {
                    "path": f"{domain_prefix}/{docs_name}.md",
                    "type": "docs",
                    "content": docs_content,
                    "domain": model_domain,
                }
            )
            logger.info(f"[File Generator] ✅ Generated {domain_prefix}/{docs_name}.md")

    return files
