"""
Centralized query configuration for wizard state management.

This module captures ALL decisions made during the conversion wizard,
providing a single source of truth for file generation.

Persisted to: temp/query_config_{query_id}.json
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass
class NamingConfiguration:
    """Computed naming prefixes - calculated once during analysis, used throughout (2-layer architecture).

    layer_folder_names is the single source of truth for folder names.
    Individual folder properties (staging_folder, etc.) are computed from this dict.
    """

    staging_model_prefix: str = "stg__"
    intermediate_model_prefix: str = "int__"
    mart_model_prefix: str = ""
    final_model_prefix: str = ""
    final_model_suffix: str = ""
    sources_file_name: str = "sources.yml"
    case_style: str = "snake_case"
    separator: str = "_"
    source_name_from: str = "table"
    use_layer_folders: bool = True

    # Single source of truth for folder names
    layer_folder_names: dict[str, str] = field(
        default_factory=lambda: {
            "staging": "staging",
            "intermediate": "intermediate",
            "marts": "marts",
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
        """Get marts folder name from layer_folder_names dict."""
        return self.layer_folder_names.get("marts", "marts")

    def __post_init__(self):
        """Validate configuration after initialization."""
        # Validate intermediate_model_prefix is not empty (required for 2-layer architecture)
        if not self.intermediate_model_prefix:
            raise ValueError(
                "intermediate_model_prefix cannot be empty. This is required for the 2-layer architecture."
            )

        # Validate case_style is valid
        valid_case_styles = ["snake_case", "camelCase", "PascalCase", "kebab-case"]
        if self.case_style not in valid_case_styles:
            raise ValueError(f"Invalid case_style: '{self.case_style}'. Must be one of: {', '.join(valid_case_styles)}")

        # Validate separator is not empty
        if not self.separator:
            raise ValueError("separator cannot be empty. Use a valid separator like '__' or '_'")

        # Validate layer_folder_names has all required layers
        required_layers = {"staging", "intermediate", "marts"}
        provided_layers = set(self.layer_folder_names.keys())
        if not required_layers.issubset(provided_layers):
            missing = required_layers - provided_layers
            raise ValueError(f"layer_folder_names missing required layers: {missing}. Must include: {required_layers}")

        # Validate folder names are not empty
        for layer, folder in self.layer_folder_names.items():
            if not folder or not folder.strip():
                raise ValueError(f"Folder name for '{layer}' cannot be empty.")

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "staging_model_prefix": self.staging_model_prefix,
            "intermediate_model_prefix": self.intermediate_model_prefix,
            "mart_model_prefix": self.mart_model_prefix,
            "final_model_prefix": self.final_model_prefix,
            "final_model_suffix": self.final_model_suffix,
            "sources_file_name": self.sources_file_name,
            "case_style": self.case_style,
            "separator": self.separator,
            "source_name_from": self.source_name_from,
            "use_layer_folders": self.use_layer_folders,
            "layer_folder_names": self.layer_folder_names,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NamingConfiguration":
        """Create from dictionary. Expects layer_folder_names dict format."""
        # Get layer_folder_names dict with defaults
        layer_folder_names = data.get(
            "layer_folder_names",
            {
                "staging": "staging",
                "intermediate": "intermediate",
                "marts": "marts",
            },
        )

        return cls(
            staging_model_prefix=data.get("staging_model_prefix", "stg__"),
            intermediate_model_prefix=data.get("intermediate_model_prefix", "int__"),
            mart_model_prefix=data.get("mart_model_prefix", ""),
            final_model_prefix=data.get("final_model_prefix", ""),
            final_model_suffix=data.get("final_model_suffix", ""),
            sources_file_name=data.get("sources_file_name", "sources.yml"),
            case_style=data.get("case_style", "snake_case"),
            separator=data.get("separator", "_"),
            source_name_from=data.get("source_name_from", "table"),
            use_layer_folders=data.get("use_layer_folders", True),
            layer_folder_names=layer_folder_names,
        )


@dataclass
class CrossProjectDecision:
    """A single cross-project reference decision."""

    original_reference: str
    use_cross_ref: bool = False
    project: str | None = None
    model: str | None = None
    dataset: str | None = None
    table: str | None = None
    suggested_source: str | None = None

    def to_dict(self) -> dict:
        return {
            "original_reference": self.original_reference,
            "use_cross_ref": self.use_cross_ref,
            "project": self.project,
            "model": self.model,
            "dataset": self.dataset,
            "table": self.table,
            "suggested_source": self.suggested_source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CrossProjectDecision":
        return cls(
            original_reference=data["original_reference"],
            use_cross_ref=data.get("use_cross_ref", False),
            project=data.get("project"),
            model=data.get("model"),
            dataset=data.get("dataset"),
            table=data.get("table"),
            suggested_source=data.get("suggested_source"),
        )


@dataclass
class ModelConfiguration:
    """Configuration for a single model (staging, intermediate, or mart)."""

    table: str  # Model name (e.g., "stg_customers", "int_churn_customers", "customer_churn")
    model_type: Literal["staging", "intermediate", "mart"] = "intermediate"
    materialization: Literal["view", "table", "incremental", "ephemeral"] = "table"
    schema: str = ""
    tags: list[str] = field(default_factory=list)
    # SQL can be stored here after transformation (optional - may be large)
    transformed_sql: str | None = None
    # Description for documentation generation (used in schema.yml and docs blocks)
    description: str | None = None

    def __post_init__(self):
        """Validate configuration after initialization."""
        # Validate model_type
        valid_types = {"staging", "intermediate", "mart"}
        if self.model_type not in valid_types:
            raise ValueError(f"Invalid model_type: '{self.model_type}'. Must be one of: {', '.join(valid_types)}")

        # Validate table name is not empty
        if not self.table or not self.table.strip():
            raise ValueError(
                "table (model name) cannot be empty. "
                "Must be a valid model name like 'int_churn_customers' or 'customer_churn'"
            )

        # Validate materialization type
        valid_materializations = {"view", "table", "incremental", "ephemeral"}
        if self.materialization not in valid_materializations:
            raise ValueError(
                f"Invalid materialization: '{self.materialization}'. "
                f"Must be one of: {', '.join(valid_materializations)}"
            )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "table": self.table,
            "model_type": self.model_type,
            "materialization": self.materialization,
            "schema": self.schema,
            "tags": self.tags,
        }
        if self.transformed_sql:
            result["transformed_sql"] = self.transformed_sql
        if self.description:
            result["description"] = self.description
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ModelConfiguration":
        """Create from dictionary."""
        # Handle legacy "prep" types by converting to "intermediate"
        model_type = data.get("model_type", "intermediate")
        if model_type in ("prep",):
            model_type = "intermediate"

        return cls(
            table=data["table"],
            model_type=model_type,
            materialization=data.get("materialization", "table"),
            schema=data.get("schema", ""),
            tags=data.get("tags", []),
            transformed_sql=data.get("transformed_sql"),
            description=data.get("description"),
        )


@dataclass
class StepCompletionState:
    """Tracks which steps have been viewed/completed."""

    analyze_viewed: bool = False
    cross_project_refs_viewed: bool = False
    final_models_viewed: bool = False
    materialization_configured: bool = False
    tags_configured: bool = False
    sources_viewed: bool = False
    review_viewed: bool = False
    deployed: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "analyze_viewed": self.analyze_viewed,
            "cross_project_refs_viewed": self.cross_project_refs_viewed,
            "final_models_viewed": self.final_models_viewed,
            "materialization_configured": self.materialization_configured,
            "tags_configured": self.tags_configured,
            "sources_viewed": self.sources_viewed,
            "review_viewed": self.review_viewed,
            "deployed": self.deployed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StepCompletionState":
        """Create from dictionary."""
        return cls(
            analyze_viewed=data.get("analyze_viewed", False),
            cross_project_refs_viewed=data.get("cross_project_refs_viewed", False),
            final_models_viewed=data.get("final_models_viewed", False),
            materialization_configured=data.get("materialization_configured", False),
            tags_configured=data.get("tags_configured", False),
            sources_viewed=data.get("sources_viewed", False),
            review_viewed=data.get("review_viewed", False),
            deployed=data.get("deployed", False),
        )


@dataclass
class QueryConfiguration:
    """
    Unified configuration object capturing ALL wizard decisions.

    This is the single source of truth for:
    - Analysis results (computed once)
    - User decisions (cross-project refs, materializations, tags)
    - Naming conventions (computed once from YAML config)
    - Step completion state

    Persisted to: temp/query_config_{query_id}.json
    """

    # Identity
    query_id: int
    query_name: str
    project_name: str | None = None  # dbt project  e.g. analytics
    domain_area: str | None = None  # Selected domain_area - e.g. customer
    model_group: str | None = None  # Selected scheduled query unique name - e.g. customer_orders

    # Version for future conversion support
    config_version: str = "1.0"

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Analysis Results (computed once, read many times)
    analysis_results: dict[str, Any] | None = None

    # Computed naming configuration (from YAML config + project_name)
    naming: NamingConfiguration = field(default_factory=NamingConfiguration)

    # Cross-project reference decisions
    cross_project_decisions: list[CrossProjectDecision] = field(default_factory=list)

    # Model configurations (materialization, schema, tags)
    model_configurations: list[ModelConfiguration] = field(default_factory=list)

    # Step completion tracking
    step_completion: StepCompletionState = field(default_factory=StepCompletionState)

    # User input from prerequisite checklist
    github_branch: str | None = None
    dbt_project_path: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "query_id": self.query_id,
            "query_name": self.query_name,
            "project_name": self.project_name,
            "domain_area": self.domain_area,
            "model_group": self.model_group,
            "model_path": self.model_path,
            "config_version": self.config_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "analysis_results": self.analysis_results,
            "naming": self.naming.to_dict(),
            "cross_project_decisions": [d.to_dict() for d in self.cross_project_decisions],
            "model_configurations": [m.to_dict() for m in self.model_configurations],
            "step_completion": self.step_completion.to_dict(),
            "github_branch": self.github_branch,
            "dbt_project_path": self.dbt_project_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QueryConfiguration":
        """Create from dictionary (JSON deserialization)."""
        naming = NamingConfiguration.from_dict(data.get("naming", {}))

        cross_project_decisions = [CrossProjectDecision.from_dict(d) for d in data.get("cross_project_decisions", [])]

        model_configurations = [ModelConfiguration.from_dict(m) for m in data.get("model_configurations", [])]

        step_completion = StepCompletionState.from_dict(data.get("step_completion", {}))

        return cls(
            query_id=data["query_id"],
            query_name=data.get("query_name", ""),
            project_name=data.get("project_name"),
            domain_area=data.get("domain_area"),
            model_group=data.get("model_group"),
            config_version=data.get("config_version", "1.0"),
            created_at=data.get("created_at", datetime.utcnow().isoformat()),
            updated_at=data.get("updated_at", datetime.utcnow().isoformat()),
            analysis_results=data.get("analysis_results"),
            naming=naming,
            cross_project_decisions=cross_project_decisions,
            model_configurations=model_configurations,
            step_completion=step_completion,
            github_branch=data.get("github_branch"),
            dbt_project_path=data.get("dbt_project_path"),
        )

    @property
    def model_path(self) -> str | None:
        if self.domain_area and self.model_group:
            return f"{self.domain_area}/{self.model_group}"
        return None

    def update_timestamp(self) -> None:
        """Update the updated_at timestamp to current time."""
        self.updated_at = datetime.utcnow().isoformat()

    def get_model_config(self, table_name: str) -> ModelConfiguration | None:
        """Get model configuration by table name."""
        for config in self.model_configurations:
            if config.table == table_name:
                return config
        return None

    def get_cross_project_decision(self, original_reference: str) -> CrossProjectDecision | None:
        """Get cross-project decision by original reference."""
        for decision in self.cross_project_decisions:
            if decision.original_reference == original_reference:
                return decision
        return None

    def get_model_config_map(self) -> dict[str, dict]:
        """Get model configurations as a lookup map (table -> config dict)."""
        return {m.table: m.to_dict() for m in self.model_configurations}

    def get_cross_project_decisions_map(self) -> dict[str, dict]:
        """Get cross-project decisions as a lookup map (original_ref -> decision dict)."""
        return {d.original_reference: d.to_dict() for d in self.cross_project_decisions}
