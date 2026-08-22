"""Dataset-based cross-project reference resolver (MVP implementation)."""

from dbt_training_wheels.config_schema import CrossProjectConfig, CrossProjectRefProject
from dbt_training_wheels.services.resolvers.base import CrossProjectResolution, CrossProjectResolver


class DatasetResolver(CrossProjectResolver):
    """Resolves cross-project refs based on dataset-to-project mapping.

    This is the MVP implementation that uses configuration to map
    BigQuery datasets to dbt projects. When a table reference uses
    a dataset that's configured as belonging to another project,
    the resolver suggests using cross-project ref() syntax.

    Example config:
        cross_project_refs:
          enabled: true
          projects:
            - name: analytics_platform
              datasets:
                - raw_customer
                - raw_orders

    When SQL references `raw_customer.dim_customer`, this resolver
    will return a resolution suggesting `{{ ref('analytics_platform', 'dim_customer') }}`.
    """

    def __init__(self, config: CrossProjectConfig):
        """Initialize the resolver with configuration.

        Args:
            config: CrossProjectConfig containing project-to-dataset mappings
        """
        self.config = config
        self._build_lookup()

    def _build_lookup(self) -> None:
        """Build dataset -> project config lookup table.

        Creates a case-insensitive mapping from dataset names to project configs
        (not just names) for efficient lookup during resolution.
        """
        self.dataset_to_project_config: dict[str, CrossProjectRefProject] = {}

        for project in self.config.projects:
            for dataset in project.datasets:
                # Store lowercase for case-insensitive matching
                # Store the entire project config so we can check source_projects
                self.dataset_to_project_config[dataset.lower()] = project

    def resolve(self, dataset: str, table: str, gcp_project: str | None = None) -> CrossProjectResolution | None:
        """Check if a table is a model in another dbt project.

        Args:
            dataset: BigQuery dataset name (e.g., "raw_customer")
            table: Table name (e.g., "dim_customer")
            gcp_project: Optional GCP project name (e.g., "my-gcp-project")
                Used to filter matches by source_projects configuration

        Returns:
            CrossProjectResolution if dataset matches a known project, None otherwise
        """
        # Lookup project config by dataset (case-insensitive)
        project_config = self.dataset_to_project_config.get(dataset.lower())

        if not project_config:
            return None

        # If source_projects is configured, verify GCP project matches
        if project_config.source_projects:
            if not gcp_project:
                # No GCP project provided, can't verify
                return None
            if gcp_project not in project_config.source_projects:
                # GCP project doesn't match configured source_projects
                return None

        return CrossProjectResolution(
            project=project_config.name,
            model=table,
            confidence="medium",  # Dataset-based matching has medium confidence
            original_dataset=dataset,
            original_table=table,
        )

    def get_known_projects(self) -> list[str]:
        """Return list of configured project names.

        Returns:
            List of dbt project names from configuration
        """
        return [p.name for p in self.config.projects]

    def get_known_datasets(self) -> list[str]:
        """Return list of all datasets mapped to projects.

        Returns:
            List of dataset names (original case from config)
        """
        datasets = []
        for project in self.config.projects:
            datasets.extend(project.datasets)
        return datasets
