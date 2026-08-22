"""Base interface and types for cross-project reference resolution."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CrossProjectResolution:
    """Result of resolving a table to a cross-project reference.

    When a table is identified as belonging to another dbt project,
    this dataclass holds the information needed to generate a
    cross-project ref() call instead of a source() call.
    """

    # dbt project name (e.g., "analytics_platform")
    project: str

    # model name (e.g., "dim_customer")
    model: str

    # Confidence level: "high" (manifest-based) or "medium" (dataset pattern)
    confidence: str

    # Original dataset name from SQL (e.g., "raw_customer")
    original_dataset: str

    # Original table name from SQL (e.g., "dim_customer")
    original_table: str

    @property
    def full_original_reference(self) -> str:
        """Return the full original reference (dataset.table)."""
        return f"{self.original_dataset}.{self.original_table}"

    @property
    def suggested_ref(self) -> str:
        """Return the suggested dbt ref() syntax."""
        return f"{{{{ ref('{self.project}', '{self.model}') }}}}"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "project": self.project,
            "model": self.model,
            "confidence": self.confidence,
            "original_dataset": self.original_dataset,
            "original_table": self.original_table,
            "full_original_reference": self.full_original_reference,
            "suggested_ref": self.suggested_ref,
        }


class CrossProjectResolver(ABC):
    """Abstract interface for detecting cross-project references.

    Implementations of this interface determine whether a table reference
    in SQL belongs to another dbt project. Different implementations can
    use different strategies:

    - DatasetResolver: Maps dataset names to projects (MVP)
    - ManifestResolver: Parses manifest.json for exact matches (future)
    - HybridResolver: Combines both approaches (future)
    """

    @abstractmethod
    def resolve(self, dataset: str, table: str, gcp_project: str | None = None) -> CrossProjectResolution | None:
        """Check if a table is a model in another dbt project.

        Args:
            dataset: BigQuery dataset name (e.g., "raw_customer")
            table: Table name (e.g., "dim_customer")
            gcp_project: Optional GCP project name (e.g., "my-gcp-project")
                Used to filter matches by source_projects configuration

        Returns:
            CrossProjectResolution if table is a known model, None otherwise
        """
        pass

    @abstractmethod
    def get_known_projects(self) -> list[str]:
        """Return list of configured project names.

        Returns:
            List of dbt project names that this resolver knows about
        """
        pass

    @abstractmethod
    def get_known_datasets(self) -> list[str]:
        """Return list of all datasets mapped to projects.

        Returns:
            List of dataset names that are mapped to dbt projects
        """
        pass
