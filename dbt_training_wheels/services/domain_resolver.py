"""Attribute generated models to business domains.

A domain is a review/ownership boundary declared in config as a set of destination
datasets. Models are attributed by the dataset their CREATE/INSERT target writes to,
so a single conversion can be recognised as spanning several domains and deployed as a
stack of dependent PRs rather than one flat PR.

Usage:
    groups = attribute_models_to_domains(analysis_results, config, project_name="reporting")
    if len(groups) > 1:
        ...  # cross-domain conversion - deploy as a stack
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import OrganizationConfig

logger = logging.getLogger(__name__)

# Domain used when a model's dataset matches nothing in config
UNASSIGNED_DOMAIN = "unassigned"

_REF_PATTERN = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")

# Tables a query reads, used to order sibling queries against each other
_CREATED_TABLE_REFERENCE = re.compile(r"(?:FROM|JOIN)\s+`?([a-zA-Z0-9_.-]+)`?", re.IGNORECASE)


@dataclass
class ModelDomain:
    """A single model and the domain it was attributed to."""

    model: str  # Bare model name, without a layer prefix
    layer: str  # "staging" | "intermediate" | "mart"
    domain: str
    dataset: str = ""
    source: str = ""  # "dataset_match" | "fallback"


@dataclass
class DomainGroup:
    """All models belonging to one domain, in dependency order relative to other groups."""

    domain: str
    models: list[ModelDomain] = field(default_factory=list)

    @property
    def model_names(self) -> list[str]:
        return [m.model for m in self.models]

    @property
    def layers(self) -> list[str]:
        """Layers present in this group, in hierarchy order."""
        present = {m.layer for m in self.models}
        return [layer for layer in ("staging", "intermediate", "mart") if layer in present]


class DomainResolver:
    """Resolves a destination dataset to a configured domain.

    Mirrors DatasetResolver in services/resolvers/, which maps datasets to dbt
    projects for cross-project refs. This maps datasets to domains within a project.
    """

    def __init__(self, config: OrganizationConfig | None, project_name: str | None = None):
        self.config = config
        self.project_name = project_name
        self._dataset_to_domain: dict[str, str] = {}
        self._build_lookup()

    def _build_lookup(self) -> None:
        """Build a case-insensitive dataset -> domain lookup for the project."""
        if not (self.config and self.project_name and self.config.projects):
            return

        project = self.config.projects.get(self.project_name)
        if not (project and project.dbt_config and project.dbt_config.domains):
            return

        for domain in project.dbt_config.domains:
            for dataset in domain.datasets:
                existing = self._dataset_to_domain.get(dataset.lower())
                if existing and existing != domain.name:
                    logger.warning(
                        f"[Domains] Dataset '{dataset}' is claimed by both '{existing}' and "
                        f"'{domain.name}'; keeping '{existing}'"
                    )
                    continue
                self._dataset_to_domain[dataset.lower()] = domain.name

        logger.info(
            f"[Domains] Loaded {len(self._dataset_to_domain)} dataset mappings for project '{self.project_name}'"
        )

    @property
    def is_configured(self) -> bool:
        """Whether any domains are configured for this project."""
        return bool(self._dataset_to_domain)

    def resolve(self, dataset: str) -> str | None:
        """Return the domain owning a dataset, or None if it isn't configured."""
        if not dataset:
            return None
        return self._dataset_to_domain.get(dataset.lower())

    def get_known_domains(self) -> list[str]:
        """Return configured domain names, in config order."""
        seen: list[str] = []
        for domain in self._dataset_to_domain.values():
            if domain not in seen:
                seen.append(domain)
        return seen


def domain_from_filename(filename: str | None) -> str:
    """
    The domain implied by where a query's file sits.

    A folder you upload is one domain; subfolders inside it are each their own domain.
    Since the upload merges every folder into `<path>.sql`, the file's stem already is
    that name:

        demo.sql              -> "demo"
        churn/customer.sql    -> "customer"
        churn/eu/claims.sql   -> "claims"

    This needs no configuration, which is the point - the folder structure the user
    chose is the domain structure.

    Args:
        filename: Query filename relative to source_sql_file/

    Returns:
        The domain name, or "" when there's no filename to derive it from
    """
    if not filename:
        return ""

    stem = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return stem[:-4] if stem.lower().endswith(".sql") else stem


def _strip_layer_prefix(model_name: str, prefixes: list[str]) -> str:
    """Remove a known layer prefix from a model name, longest prefix first."""
    for prefix in sorted((p for p in prefixes if p), key=len, reverse=True):
        if model_name.startswith(prefix):
            return model_name[len(prefix) :]
    return model_name


def _order_domains(
    domains: list[str],
    dependencies: dict[str, set[str]],
) -> list[str]:
    """Topologically sort domains so dependencies come first.

    Ties are broken by first appearance, which keeps the output stable. A cycle means
    two domains reference each other, which can't be expressed as a stack - in that
    case the original order is returned and the caller is warned.
    """
    ordered: list[str] = []
    remaining = list(domains)

    while remaining:
        ready = [d for d in remaining if not (dependencies.get(d, set()) & set(remaining))]
        if not ready:
            logger.warning(
                f"[Domains] Circular dependency between domains {remaining}; falling back to declaration order"
            )
            ordered.extend(remaining)
            break
        ordered.extend(ready)
        remaining = [d for d in remaining if d not in ready]

    return ordered


def attribute_models_to_domains(
    analysis_results: dict[str, Any],
    config: OrganizationConfig | None = None,
    project_name: str | None = None,
    fallback_domain: str = "",
    query_filename: str | None = None,
) -> list[DomainGroup]:
    """
    Group the models in an analysis result by domain, in dependency order.

    Domain comes from folder structure by default: the folder a query was uploaded
    from is its domain, and subfolders are their own domains (see
    domain_from_filename). Configured dataset -> domain mappings, if any, override
    that per model - for the case where one folder legitimately spans domains.

    Args:
        analysis_results: Output of analyze_query(), which carries layerClassification
            (each component tagged with its destination dataset) and naming prefixes
        config: Organization configuration holding optional domain definitions
        project_name: Project whose domains apply
        fallback_domain: Used when neither the folder nor config gives a domain -
            usually the user's chosen domain_area. Defaults to "unassigned".
        query_filename: Query filename relative to source_sql_file/, the folder-derived
            domain source

    Returns:
        One DomainGroup per domain, ordered so that a domain comes after any domain it
        depends on. A single group means the conversion doesn't span domains.
    """
    resolver = DomainResolver(config, project_name)
    folder_domain = domain_from_filename(query_filename)
    fallback = folder_domain or fallback_domain or UNASSIGNED_DOMAIN

    layer_classification = analysis_results.get("layerClassification") or {}
    naming = analysis_results.get("naming") or {}
    prefixes = [
        naming.get("stagingModelPrefix", ""),
        naming.get("intermediateModelPrefix", ""),
        naming.get("martModelPrefix", ""),
    ]

    # Attribute every model, preserving layer order so groups read staging -> mart
    attributed: list[ModelDomain] = []
    domain_of_model: dict[str, str] = {}

    for layer in ("staging", "intermediate", "mart"):
        for component in layer_classification.get(layer, []):
            name = component.get("name")
            if not name:
                continue

            dataset = component.get("dataset", "")
            # Configured dataset mappings refine the folder-derived domain
            resolved = resolver.resolve(dataset)
            attributed.append(
                ModelDomain(
                    model=name,
                    layer=layer,
                    domain=resolved or fallback,
                    dataset=dataset,
                    source="dataset_match" if resolved else ("folder" if folder_domain else "fallback"),
                )
            )
            # A table appears in both its structural layer and mart; same domain either way
            domain_of_model[name] = resolved or fallback

    if not attributed:
        return []

    # Build domain-level dependencies from the ref() calls in each model's SQL
    dependencies: dict[str, set[str]] = {}
    for layer in ("staging", "intermediate", "mart"):
        for component in layer_classification.get(layer, []):
            name = component.get("name")
            if not name:
                continue

            domain = domain_of_model.get(name)
            sql = component.get("transformedSql") or component.get("sql") or ""
            for referenced in _REF_PATTERN.findall(sql):
                # Cross-project refs are ref('project', 'model') and don't match here
                target = _strip_layer_prefix(referenced, prefixes)
                target_domain = domain_of_model.get(target)
                if target_domain and domain and target_domain != domain:
                    dependencies.setdefault(domain, set()).add(target_domain)

    # Preserve first-appearance order, then sort by dependency
    first_seen: list[str] = []
    for model in attributed:
        if model.domain not in first_seen:
            first_seen.append(model.domain)

    groups = {domain: DomainGroup(domain=domain) for domain in first_seen}
    for model in attributed:
        groups[model.domain].models.append(model)

    ordered = _order_domains(first_seen, dependencies)

    logger.info(f"[Domains] Attributed {len(attributed)} models across {len(ordered)} domain(s): {', '.join(ordered)}")
    if not resolver.is_configured:
        logger.info(f"[Domains] No domains configured for project '{project_name}', using '{fallback}' for all models")

    return [groups[domain] for domain in ordered]


def _sibling_dependencies(
    queries: list[dict[str, Any]],
) -> tuple[list[str], dict[str, set[str]]]:
    """
    Which sibling queries read tables that other sibling queries create.

    Args:
        queries: Sibling query dicts, each with 'sql' and 'filename'

    Returns:
        (keys in input order, {key: keys it depends on}). Keys are filenames; a query
        with no dependencies is absent from the mapping rather than mapped to an
        empty set.
    """
    from dbt_training_wheels.utils.sql_parser import extract_destination_datasets

    # What each query creates, and which query creates each table
    creator_of: dict[str, str] = {}
    keys = []
    for query in queries:
        key = query.get("filename") or ""
        keys.append(key)
        for table in extract_destination_datasets(query.get("sql", "")):
            creator_of.setdefault(table.lower(), key)

    # A query depends on whoever creates a table it reads (and doesn't create itself)
    dependencies: dict[str, set[str]] = {}
    for query in queries:
        key = query.get("filename") or ""
        own_tables = {t.lower() for t in extract_destination_datasets(query.get("sql", ""))}
        for referenced in _CREATED_TABLE_REFERENCE.findall(query.get("sql", "")):
            table = referenced.split(".")[-1].lower()
            if table in own_tables:
                continue
            producer = creator_of.get(table)
            if producer and producer != key:
                dependencies.setdefault(key, set()).add(producer)

    return keys, dependencies


def group_related_queries(queries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """
    Split sibling queries into groups that feed off each other.

    An upload of subfolders usually isn't one piece of work. Some subfolders read
    tables that others create - those belong together, and have to merge in order.
    Subfolders that share nothing are independent and shouldn't be made to wait on
    each other.

    Grouping is by connected component, treating the dependency edge as undirected:
    if A creates a table B reads, both are in one group, and so is anything reaching
    either of them. That is the transitive closure of "feeds off", which is what makes
    a group safe to deploy as a unit and safe to deploy separately from other groups.

    Args:
        queries: Sibling query dicts, each with 'sql' and 'filename'

    Returns:
        Groups, each ordered bottom-first the same way order_sibling_queries orders a
        whole upload. Groups come in order of their earliest member, so the output is
        stable and matches the order the subfolders were read in. A query that shares
        nothing with the others is a group of one.
    """
    keys, dependencies = _sibling_dependencies(queries)

    # Undirected adjacency - direction decides order within a group, not membership
    neighbours: dict[str, set[str]] = {key: set() for key in keys}
    for key, depends_on in dependencies.items():
        for producer in depends_on:
            if key in neighbours and producer in neighbours:
                neighbours[key].add(producer)
                neighbours[producer].add(key)

    by_key = {query.get("filename") or "": query for query in queries}
    position = {key: index for index, key in enumerate(keys)}

    seen: set[str] = set()
    groups: list[list[dict[str, Any]]] = []

    for key in keys:  # input order, so groups come out in first-appearance order
        if key in seen:
            continue

        component: list[str] = []
        frontier = [key]
        seen.add(key)
        while frontier:
            current = frontier.pop()
            component.append(current)
            for neighbour in neighbours[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    frontier.append(neighbour)

        # Order within the group by dependency, restricted to this group's members
        member_set = set(component)
        component.sort(key=lambda k: position[k])
        scoped = {k: (dependencies.get(k, set()) & member_set) for k in component}
        groups.append([by_key[k] for k in _order_domains(component, scoped)])

    if len(groups) > 1:
        summary = " | ".join(", ".join(domain_from_filename(q.get("filename")) for q in group) for group in groups)
        logger.info(f"[Siblings] {len(groups)} independent groups: {summary}")

    return groups


def order_sibling_queries(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Order sibling queries so a query comes after every query it reads from.

    Each subfolder of an upload is its own query, but they can reference each other's
    tables (see sibling references). That makes them a stack: the query producing a
    table has to merge before the query consuming it.

    Args:
        queries: Sibling query dicts, each with 'sql' and 'filename'

    Returns:
        The same queries, ordered bottom of the stack first. Queries with no
        relationship keep their input order; a cycle falls back to input order.
    """
    keys, dependencies = _sibling_dependencies(queries)
    ordered_keys = _order_domains(keys, dependencies)
    by_key = {query.get("filename") or "": query for query in queries}

    logger.info(f"[Siblings] Deploy order: {' -> '.join(ordered_keys)}")
    return [by_key[key] for key in ordered_keys]


def group_files_by_domain(
    files: list[dict[str, Any]],
    groups: list[DomainGroup],
    analysis_results: dict[str, Any],
) -> list[list[dict[str, Any]]]:
    """
    Split generated files into one bucket per domain group, preserving group order.

    Model files follow their model's domain. Files shared across the whole conversion
    are placed where they can't break an intermediate state:

    - sources.yml goes in the first bucket, because models reference the sources it
      defines and would fail to parse without them
    - schema.yml and the docs markdown go in the last bucket, because they describe
      every model in the conversion and would reference models that don't exist yet

    Splitting these shared files per domain is not yet supported.

    Args:
        files: Output of generate_files_for_query()
        groups: Domain groups from attribute_models_to_domains(), in merge order
        analysis_results: Used for the layer prefixes that map a file back to a model

    Returns:
        One list of files per group, in the same order as `groups`
    """
    if not groups:
        return []

    naming = analysis_results.get("naming") or {}
    prefix_for_layer = {
        "staging": naming.get("stagingModelPrefix", ""),
        "intermediate": naming.get("intermediateModelPrefix", ""),
        "mart": naming.get("martModelPrefix", ""),
    }

    # Prefixed model file name -> index of the group that owns it
    owner_of: dict[str, int] = {}
    for index, group in enumerate(groups):
        for model in group.models:
            owner_of[f"{prefix_for_layer.get(model.layer, '')}{model.model}"] = index

    buckets: list[list[dict[str, Any]]] = [[] for _ in groups]
    first, last = 0, len(groups) - 1
    index_of_domain = {group.domain: index for index, group in enumerate(groups)}

    for file_info in files:
        path = file_info.get("path", "")
        if not path:
            continue

        # The generator tags files with the domain it wrote them under, which beats
        # inferring one from the filename
        tagged = file_info.get("domain")
        if tagged in index_of_domain:
            buckets[index_of_domain[tagged]].append(file_info)
            continue

        file_name = path.rsplit("/", 1)[-1]
        stem = file_name.rsplit(".", 1)[0]

        if file_info.get("type") == "model":
            index = owner_of.get(stem)
            if index is None:
                # Unattributed model - put it last so anything it references exists
                logger.warning(f"[Domains] Could not attribute model file '{path}', placing it at the top of the stack")
                index = last
        elif file_name.startswith("sources"):
            index = first
        else:
            index = last

        buckets[index].append(file_info)

    for group, bucket in zip(groups, buckets):
        logger.info(f"[Domains] {group.domain}: {len(bucket)} file(s)")

    return buckets
