"""Service for cross-project reference detection and management.

This service orchestrates the detection of tables that belong to other dbt projects
and should use cross-project ref() syntax instead of source() syntax.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from dbt_training_wheels.services.resolvers import create_resolver
from dbt_training_wheels.storage import FileSystemStorage

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import OrganizationConfig

logger = logging.getLogger(__name__)


class CrossProjectService:
    """Service for detecting and managing cross-project references.

    This service:
    - Detects tables that belong to other dbt projects
    - Stores user decisions about which refs to use
    - Provides data for file generation
    """

    def __init__(self, config: "OrganizationConfig", storage: FileSystemStorage | None = None):
        """Initialize the service.

        Args:
            config: Organization configuration with cross_project_refs settings
            storage: Optional storage instance (defaults to FileSystemStorage)
        """
        self.config = config
        self.storage = storage or FileSystemStorage()

        # Initialize resolver if cross-project refs are enabled
        self.resolver = None
        if config.cross_project_refs and config.cross_project_refs.enabled:
            self.resolver = create_resolver(config.cross_project_refs)

        # Cache for public models scan (avoids rescanning GitHub repeatedly)
        self._public_models_cache: dict[str, list[str]] | None = None

    @property
    def is_enabled(self) -> bool:
        """Check if cross-project ref detection is enabled."""
        return self.resolver is not None

    def detect_cross_project_refs(self, query: dict, hardcoded_tables: list[dict[Any, Any]] | None = None) -> dict:
        """Detect tables that may be cross-project references.

        Args:
            query: Query dict containing query metadata
            hardcoded_tables: List of table info dicts from analyze_sql_content()
                Each dict should contain:
                - 'table': Full qualified table name (e.g., 'project.dataset.table')
                - 'sourceSchema': The dataset name
                - 'sourceTable': The table name

        Returns:
            Dict with:
                - cross_project_refs: List of detected cross-project refs
                - sources: List of tables that should remain as sources
                - summary: Count summary
        """
        if not self.is_enabled:
            return self._empty_result(query, hardcoded_tables)

        # At this point, resolver must be available (guaranteed by is_enabled check)
        assert self.resolver is not None, "Resolver should be initialized when feature is enabled"

        # Scan for public models ONCE at the start (cached)
        public_models_map = self._get_public_models()

        # Use hardcoded_tables from analysis, which contains full table references
        # This is the correct data source for detecting cross-project refs
        tables = hardcoded_tables or []
        cross_project_refs = []
        sources = []

        for table_info in tables:
            # Skip self-references (tables created within the same script)
            if table_info.get("isSelfReference"):
                continue

            # Get dataset and table from the analyzed table info
            dataset = table_info.get("sourceSchema")
            table = table_info.get("sourceTable")
            full_table_ref = table_info.get("table", "")

            if not dataset or not table:
                # No dataset specified, treat as source
                sources.append(
                    {
                        "original_reference": full_table_ref,
                        "suggested_source": table_info.get("suggestedSource", self._suggest_source(full_table_ref)),
                        "use_cross_ref": False,
                    }
                )
                continue

            # Extract GCP project from full table reference (project.dataset.table)
            gcp_project = None
            if full_table_ref:
                parts = full_table_ref.replace("`", "").replace('"', "").split(".")
                if len(parts) >= 3:
                    gcp_project = parts[0]

            # Try to resolve as cross-project ref, passing GCP project for source_projects check
            resolution = self.resolver.resolve(dataset, table, gcp_project=gcp_project)

            if resolution:
                # Check if model is verified as public using cached results
                project_models = public_models_map.get(resolution.project, [])
                is_public = resolution.model in project_models

                if is_public:
                    # Model is verified public - use cross-project ref
                    cross_project_refs.append(
                        {
                            "original_reference": resolution.full_original_reference,
                            "project": resolution.project,
                            "model": resolution.model,
                            "dataset": dataset,
                            "table": table,
                            "suggested_ref": resolution.suggested_ref,
                            "suggested_source": table_info.get(
                                "suggestedSource", self._suggest_source(f"{dataset}.{table}")
                            ),
                            "use_cross_ref": True,
                        }
                    )
                    logger.info(
                        f"Detected cross-project ref: {resolution.full_original_reference} -> {resolution.suggested_ref}"
                    )
                else:
                    # Model not verified as public - fall back to source
                    sources.append(
                        {
                            "original_reference": f"{dataset}.{table}",
                            "suggested_source": table_info.get(
                                "suggestedSource", self._suggest_source(f"{dataset}.{table}")
                            ),
                            "use_cross_ref": False,
                        }
                    )
                    logger.debug(
                        f"Model '{resolution.model}' not public in project '{resolution.project}', using source"
                    )
            else:
                sources.append(
                    {
                        "original_reference": f"{dataset}.{table}",
                        "suggested_source": table_info.get(
                            "suggestedSource", self._suggest_source(f"{dataset}.{table}")
                        ),
                        "use_cross_ref": False,
                    }
                )

        return {
            "query_id": query.get("id"),
            "cross_project_refs": cross_project_refs,
            "sources": sources,
            "summary": {
                "total_tables": len(tables),
                "cross_project_refs": len(cross_project_refs),
                "sources": len(sources),
            },
        }

    def _parse_table_ref(self, table_ref: str) -> tuple[str | None, str]:
        """Parse a table reference into dataset and table name.

        Args:
            table_ref: Table reference (e.g., "dataset.table" or "project.dataset.table")

        Returns:
            Tuple of (dataset, table). Dataset may be None if not present.
        """
        parts = table_ref.replace("`", "").replace('"', "").split(".")

        if len(parts) >= 3:
            # project.dataset.table - use dataset
            return parts[-2], parts[-1]
        elif len(parts) == 2:
            # dataset.table
            return parts[0], parts[1]
        else:
            # Just table name
            return None, parts[0]

    def _suggest_source(self, table_ref: str) -> str:
        """Generate suggested source() syntax for a table reference.

        Args:
            table_ref: Original table reference

        Returns:
            Suggested dbt source() syntax
        """
        dataset, table = self._parse_table_ref(table_ref)
        if dataset:
            return f"{{{{ source('{dataset}', '{table}') }}}}"
        return f"{{{{ source('unknown', '{table}') }}}}"

    def _empty_result(self, query: dict, hardcoded_tables: list[dict[Any, Any]] | None = None) -> dict:
        """Return empty result when feature is disabled."""
        tables = hardcoded_tables or []
        # Filter out self-references
        tables = [t for t in tables if not t.get("isSelfReference")]
        return {
            "query_id": query.get("id"),
            "cross_project_refs": [],
            "sources": [
                {
                    "original_reference": f"{t.get('sourceSchema', '')}.{t.get('sourceTable', '')}",
                    "suggested_source": t.get("suggestedSource", ""),
                    "use_cross_ref": False,
                }
                for t in tables
            ],
            "summary": {
                "total_tables": len(tables),
                "cross_project_refs": 0,
                "sources": len(tables),
            },
        }

    def save_decisions(self, query_id: int, decisions: list[dict]) -> bool:
        """Save user decisions about cross-project refs.

        Args:
            query_id: Query identifier
            decisions: List of decision dicts with:
                - original_reference: The table reference
                - use_cross_ref: Boolean - whether to use cross-project ref
                - project: (optional) Project name if using cross-ref
                - model: (optional) Model name if using cross-ref

        Returns:
            True if save was successful
        """
        filename = f"cross_project_refs_{query_id}.json"
        content = json.dumps(decisions, indent=2)
        self.storage.save_temp_file(filename, content)
        logger.debug(f"Saved cross-project ref decisions for query {query_id}")
        return True

    def load_decisions(self, query_id: int) -> list[dict[Any, Any]] | None:
        """Load saved cross-project ref decisions.

        Args:
            query_id: Query identifier

        Returns:
            List of decision dicts if found, None otherwise
        """
        filename = f"cross_project_refs_{query_id}.json"
        content = self.storage.read_temp_file(filename)

        if not content:
            return None

        try:
            from typing import cast

            result = json.loads(content)
            return cast(list[dict[Any, Any]], result)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in cross-project refs file for query {query_id}")
            return None

    def get_decisions_lookup(self, query_id: int) -> dict[str, dict]:
        """Get decisions as a lookup by original reference.

        Args:
            query_id: Query identifier

        Returns:
            Dict mapping original_reference to decision dict
        """
        decisions = self.load_decisions(query_id)
        if not decisions:
            return {}

        return {d["original_reference"]: d for d in decisions}

    def get_known_projects(self) -> list[str]:
        """Get list of known project names from configuration.

        Returns:
            List of project names, empty if feature is disabled
        """
        if not self.is_enabled:
            return []
        assert self.resolver is not None
        return self.resolver.get_known_projects()

    def get_known_datasets(self) -> list[str]:
        """Get list of all datasets mapped to projects.

        Returns:
            List of dataset names, empty if feature is disabled
        """
        if not self.is_enabled:
            return []
        assert self.resolver is not None
        return self.resolver.get_known_datasets()

    def _scan_via_git_clone(self, repository: str, base_path: str, project_name: str) -> list[str]:
        """
        Scan public models by cloning repo with SSH keys (no GitHub token needed!).

        Args:
            repository: GitHub repo in "owner/repo" format
            base_path: Path within repo to scan (e.g., "dbt_projects/analytics_platform")
            project_name: Project name for logging

        Returns:
            List of public model names
        """
        import subprocess
        import tempfile
        from pathlib import Path

        # Convert to SSH URL
        ssh_url = f"git@github.com:{repository}.git"
        logger.info(f"Cloning {ssh_url} to scan '{project_name}' (using SSH keys)")

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                # Clone with SSH (uses mounted SSH keys automatically!)
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--quiet", ssh_url, tmpdir],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,  # 60 second timeout
                )

                # Scan the base_path within cloned repo
                scan_path = Path(tmpdir) / base_path
                if not scan_path.exists():
                    logger.warning(f"Path {base_path} not found in cloned repo for project '{project_name}'")
                    return []

                # Use existing scan function on cloned files
                from dbt_training_wheels.services.file_generator import scan_public_models

                public_models = scan_public_models(str(scan_path))
                return list(public_models)  # Convert set to list

            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to clone {ssh_url}: {e.stderr}")
                raise
            except subprocess.TimeoutExpired:
                logger.error(f"Git clone timed out for {ssh_url}")
                raise
            except Exception as e:
                logger.error(f"Failed to scan via git clone: {e}")
                raise

    def _get_public_models(self) -> dict[str, list[str]]:
        """Get public models map with caching.

        Returns:
            Dict mapping project names to lists of public model names
        """
        if self._public_models_cache is None:
            self._public_models_cache = self.scan_all_public_models()
        return self._public_models_cache

    def scan_all_public_models(self) -> dict[str, list[str]]:
        """Scan all configured projects for models with access: public.

        Uses GitHub API to scan remotely when github is configured.

        Returns:
            Dict mapping project names to lists of public model names
        """
        result: dict[str, list[str]] = {}

        if not self.config.cross_project_refs or not self.config.cross_project_refs.projects:
            logger.debug("No cross-project refs configured")
            return result

        # Get repository for SSH cloning
        repository = self.config.github.repository if self.config.github else None

        for project in self.config.cross_project_refs.projects:
            project_name = project.name
            github_base_path = project.github_base_path

            logger.info(f"Scanning project '{project_name}' with github_base_path='{github_base_path}'")

            if not github_base_path:
                logger.warning(f"No github_base_path configured for project '{project_name}', skipping scan")
                result[project_name] = []
                continue

            # Try 1: Git clone with SSH (uses mounted SSH keys - no token needed!)
            if repository:
                try:
                    logger.info(f"→ Scanning '{project_name}' via SSH clone")
                    public_models = self._scan_via_git_clone(repository, github_base_path, project_name)
                    result[project_name] = sorted(public_models)
                    logger.info(f"✓ Found {len(public_models)} models: {result[project_name]}")
                    continue
                except Exception as e:
                    logger.warning(f"✗ SSH clone failed for '{project_name}': {e}")

            # Try 2: Local scanning (fallback if SSH fails)
            try:
                logger.info(f"→ Falling back to local scanning for '{project_name}'")
                from dbt_training_wheels.services.file_generator import scan_public_models

                public_models_set = scan_public_models(github_base_path)
                result[project_name] = sorted(public_models_set)
                logger.info(f"✓ Found {len(public_models_set)} models locally: {result[project_name]}")
            except Exception as e:
                logger.error(f"✗ All scanning methods failed for '{project_name}': {e}")
                result[project_name] = []

        return result

    def is_model_public(self, project_name: str, model_name: str) -> bool:
        """Check if a specific model is marked as public.

        Args:
            project_name: Name of the dbt project
            model_name: Name of the model to check

        Returns:
            True if the model is public, False otherwise or if unknown
        """
        public_models = self._get_public_models()  # Use cached version
        project_models = public_models.get(project_name, [])
        return model_name in project_models


def get_cross_project_service(config: "OrganizationConfig") -> CrossProjectService:
    """Factory function to create CrossProjectService.

    Args:
        config: Organization configuration

    Returns:
        CrossProjectService instance
    """
    return CrossProjectService(config)
