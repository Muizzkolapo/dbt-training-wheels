"""Service for loading and managing SQL queries."""

import os
import re
from datetime import datetime
from typing import TYPE_CHECKING

from dbt_training_wheels.config import SQL_DIRECTORY
from dbt_training_wheels.exceptions import FileSystemError, SQLParseError
from dbt_training_wheels.utils.sql_parser import parse_sql_file

if TYPE_CHECKING:
    from dbt_training_wheels.config_schema import OrganizationConfig


def load_queries_from_directory(config: "OrganizationConfig | None" = None) -> list[dict]:
    """
    Load all SQL files from the source_sql_file directory.

    Args:
        config: Optional OrganizationConfig for customizing parsing

    Returns:
        List of query dictionaries with metadata and content
    """
    if not os.path.exists(SQL_DIRECTORY):
        os.makedirs(SQL_DIRECTORY)
        return []

    sql_files = []
    for root, _dirs, files in os.walk(SQL_DIRECTORY):
        for filename in files:
            if filename.endswith(".sql"):
                full_path = os.path.join(root, filename)
                relative_path = os.path.relpath(full_path, SQL_DIRECTORY)
                sql_files.append(relative_path)

    sql_files = sorted(sql_files)

    queries = []
    for idx, filename in enumerate(sql_files, start=1):
        filepath = os.path.join(SQL_DIRECTORY, filename)
        try:
            query_data = parse_sql_file(filepath, config)
            folder = os.path.dirname(filename) or "Root"
            queries.append(
                {
                    "id": idx,
                    "name": query_data["name"],
                    "dataset": query_data["dataset"] or "No dataset specified",
                    "schedule": query_data["schedule"],
                    "lastRun": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "SUCCEEDED",
                    "insertCount": query_data["insertCount"],
                    "tables": query_data["tables"],
                    "sql": query_data["sql"],
                    "filename": filename,
                    "folder": folder,
                }
            )
        except FileNotFoundError as err:
            raise FileSystemError.file_not_found(filepath) from err
        except PermissionError as err:
            raise FileSystemError.permission_denied(filepath) from err
        except UnicodeDecodeError as e:
            raise SQLParseError(
                user_message=f"We couldn't read the file '{filename}'",
                beginner_help="The file might be corrupted or not a valid text file",
                common_fixes=[
                    "Make sure the file is a valid SQL text file",
                    "Try opening the file in a text editor to verify it's readable",
                    "Re-upload the file if it might be corrupted",
                ],
                docs_anchor="sql-parsing-errors",
                technical_message=f"Unicode decode error in {filename}: {str(e)}",
            ) from e
        except Exception as e:
            from dbt_training_wheels.exceptions.dbt_training_wheels_exceptions import DbtTrainingWheelsException

            if isinstance(e, DbtTrainingWheelsException):
                raise

            raise SQLParseError.syntax_error(details=f"Error parsing {filename}: {str(e)}") from e

    return queries


def conversion_name_for(filename: str | None) -> str:
    """
    The conversion a query belongs to: the folder it was uploaded from.

    'demo/customer.sql' -> 'demo'; a root-level 'lone.sql' is its own conversion,
    'lone'. One upload is one conversion, whatever it contains.
    """
    if not filename:
        return ""

    normalized = filename.replace(os.sep, "/")
    if "/" in normalized:
        return normalized.split("/", 1)[0]

    stem = normalized.rsplit("/", 1)[-1]
    return stem[:-4] if stem.lower().endswith(".sql") else stem


def conversion_tag_for(filename: str | None) -> str:
    """
    The dbt tag stamped on every model this conversion generates.

    Models are written to models/<domain>/ with no conversion segment, so
    nothing in the output layout says which conversion a model came from. This tag does,
    and it spans domains - so `dbt run --select tag:conversion_churn` picks up the whole
    conversion however many domains it wrote to.

    Prefixed because generated models already carry layer/domain tags, and a bare
    conversion name could collide with a user's own tag.
    """
    conversion = conversion_name_for(filename)
    if not conversion:
        return ""

    sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", conversion).strip("_").lower()
    return f"conversion_{sanitized}" if sanitized else ""


def load_conversions(config: "OrganizationConfig | None" = None) -> list[dict]:
    """
    Load every uploaded conversion, each with its domain queries in deploy order.

    A conversion is the unit of work: one uploaded folder, one entry in the sidebar,
    one flow, one deploy. Its subfolders are domains - outputs of the conversion, not
    things to navigate between.

    Args:
        config: Optional OrganizationConfig for parsing

    Returns:
        List of conversion dicts with 'name', 'queries' (ordered), 'domains', and
        'groups' - the domains split into sets that feed off each other
    """
    from dbt_training_wheels.services.domain_resolver import (
        domain_from_filename,
        group_related_queries,
        order_sibling_queries,
    )

    grouped: dict[str, list[dict]] = {}
    for query in load_queries_from_directory(config):
        grouped.setdefault(conversion_name_for(query.get("filename")), []).append(query)

    conversions = []
    for name in sorted(grouped):
        # Ordered so a domain reading another's table comes after it
        ordered = order_sibling_queries(grouped[name]) if len(grouped[name]) > 1 else grouped[name]

        # Domains that feed off each other have to merge in order; domains that share
        # nothing don't, and deploy separately rather than queueing behind each other
        groups = group_related_queries(ordered) if len(ordered) > 1 else [ordered]

        conversions.append(
            {
                "name": name,
                "queries": ordered,
                "domains": [domain_from_filename(q.get("filename")) for q in ordered],
                "query_ids": [q["id"] for q in ordered],
                "primary_query_id": ordered[0]["id"] if ordered else None,
                # Lightweight: ids and domain names only, no SQL - this crosses the wire
                "groups": [
                    {
                        "domains": [domain_from_filename(q.get("filename")) for q in group],
                        "query_ids": [q["id"] for q in group],
                    }
                    for group in groups
                ],
            }
        )

    return conversions


def grouped_source_files(conversion: dict) -> list[dict]:
    """
    Lay an uploaded folder out by group, ready to be zipped and downloaded.

    Nothing here is converted. The SQL is exactly what was uploaded - the value is the
    grouping: which subfolders feed off each other, and in what order they have to run.
    That's worth having on its own, before or without any move to dbt.

    Files are numbered inside a group because the order is the point. Groups are
    numbered because they're independent - group 2 doesn't wait on group 1.

        churn/
        |- GROUPS.md          <- what was grouped, and why
        |- group-1/
        |  |- 01_base.sql     <- creates the table 02 reads
        |  |- 02_features.sql
        |- group-2/
           |- 01_claims.sql   <- shares nothing with group 1

    Args:
        conversion: A conversion dict from load_conversions()

    Returns:
        List of {'path', 'content'} dicts, paths relative to the folder root
    """
    from dbt_training_wheels.services.domain_resolver import domain_from_filename, group_related_queries

    queries = conversion.get("queries") or []
    if not queries:
        return []

    groups = group_related_queries(queries) if len(queries) > 1 else [queries]

    files = [{"path": "GROUPS.md", "content": _groups_readme(conversion, groups)}]

    for group_number, group in enumerate(groups, start=1):
        # A single group means the whole upload is one unit - no point in a folder
        # called "group-1" that everything sits in
        prefix = f"group-{group_number}/" if len(groups) > 1 else ""
        for position, query in enumerate(group, start=1):
            domain = domain_from_filename(query.get("filename")) or query.get("name") or "query"
            name = f"{position:02d}_{domain}.sql" if len(group) > 1 else f"{domain}.sql"
            files.append({"path": f"{prefix}{name}", "content": query.get("sql") or ""})

    return files


def _groups_readme(conversion: dict, groups: list[list[dict]]) -> str:
    """A short note explaining the split, so the zip isn't just folders of SQL."""
    from dbt_training_wheels.services.domain_resolver import domain_from_filename

    def domains_of(group):
        return [domain_from_filename(q.get("filename")) or q.get("name") or "query" for q in group]

    lines = [f"# {conversion.get('name', 'Upload')}", ""]

    if len(groups) == 1:
        names = domains_of(groups[0])
        if len(names) == 1:
            lines += ["A single query, nothing to group."]
        else:
            lines += [
                "Every part of this upload feeds off the others, so it's one group.",
                "Run them in the order they're numbered:",
                "",
                *(f"{i}. `{name}`" for i, name in enumerate(names, start=1)),
            ]
        return "\n".join(lines) + "\n"

    lines += [
        f"{sum(len(g) for g in groups)} queries split into {len(groups)} independent groups.",
        "",
        "A group is a set of queries that read each other's tables, so they have to run",
        "in order. Different groups share nothing - run them in any order, or in",
        "parallel, and review them separately.",
        "",
    ]

    for group_number, group in enumerate(groups, start=1):
        names = domains_of(group)
        lines.append(f"## group-{group_number}")
        if len(names) == 1:
            lines += ["", f"`{names[0]}` on its own - it shares no tables with anything else here.", ""]
        else:
            lines += ["", "Run in this order:", ""]
            lines += [f"{i}. `{name}`" for i, name in enumerate(names, start=1)]
            lines += ["", f"`{names[-1]}` reads a table `{names[0]}` creates.", ""]

    return "\n".join(lines) + "\n"


def get_conversion_for_query(query_id: int, config: "OrganizationConfig | None" = None) -> dict | None:
    """The conversion a query belongs to, with all its sibling domains."""
    for conversion in load_conversions(config):
        if query_id in conversion["query_ids"]:
            return conversion
    return None


def get_sibling_queries(query: dict, config: "OrganizationConfig | None" = None) -> list[dict]:
    """
    The queries that came from the same uploaded folder, including this one.

    Each subfolder of an upload becomes its own query, but they're one conversion - so
    deploying them together as a stack keeps them reviewable in dependency order.
    Siblings are the queries whose filename shares this one's top-level folder;
    a root-level file has no siblings.

    Args:
        query: Query dict with a 'filename' relative to source_sql_file/
        config: Optional OrganizationConfig for parsing

    Returns:
        Sibling queries sorted by filename, or [] when the query stands alone
    """
    filename = (query.get("filename") or "").replace(os.sep, "/")
    if "/" not in filename:
        return []

    top_folder = filename.split("/", 1)[0] + "/"
    siblings = [
        candidate
        for candidate in load_queries_from_directory(config)
        if (candidate.get("filename") or "").replace(os.sep, "/").startswith(top_folder)
    ]

    return sorted(siblings, key=lambda q: q.get("filename") or "")


def get_sibling_created_tables(query: dict) -> set[str]:
    """
    Short names of tables created by sibling queries in the same uploaded folder.

    Each subfolder of an uploaded folder becomes its own query, but they're all one
    conversion into one dbt project - so a table one subfolder creates and another
    reads must become a ref() call, not a source() call. Siblings are the other .sql
    files under the same top-level folder in source_sql_file/; root-level files have
    no siblings.

    Args:
        query: Query dict with a 'filename' relative to source_sql_file/

    Returns:
        Set of short table names created by sibling queries
    """
    from dbt_training_wheels.utils.sql_parser import extract_destination_datasets

    filename = (query.get("filename") or "").replace(os.sep, "/")
    if "/" not in filename:
        return set()

    top_dir = os.path.join(SQL_DIRECTORY, filename.split("/", 1)[0])
    tables: set[str] = set()

    for root, _dirs, files in os.walk(top_dir):
        for name in files:
            if not name.endswith(".sql"):
                continue
            path = os.path.join(root, name)
            relative = os.path.relpath(path, SQL_DIRECTORY).replace(os.sep, "/")
            if relative == filename:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            tables.update(extract_destination_datasets(content).keys())

    return tables


def get_query_by_id(query_id: int, config: "OrganizationConfig | None" = None) -> dict | None:
    """
    Get a specific query by ID.

    Args:
        query_id: The query ID to retrieve
        config: Optional OrganizationConfig for customizing parsing

    Returns:
        Query dictionary or None if not found
    """
    queries = load_queries_from_directory(config)
    return next((q for q in queries if q["id"] == query_id), None)
