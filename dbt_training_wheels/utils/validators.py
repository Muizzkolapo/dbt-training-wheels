"""
Input validation utilities for DBT Training Wheels.
All validators raise ValidationError with beginner-friendly messages.
"""

import os
import re

from dbt_training_wheels.exceptions.dbt_training_wheels_exceptions import ValidationError


def validate_safe_path(path: str, base_dir: str | None = None) -> str:
    """
    Validate that a path doesn't contain directory traversal sequences.

    This prevents path traversal attacks where malicious input like
    '../../../etc/passwd' could access files outside intended directories.

    Args:
        path: The path string to validate
        base_dir: Optional base directory - if provided, validates the resolved
                  path stays within this directory

    Raises:
        ValidationError: If path contains traversal sequences or escapes base_dir

    Returns:
        str: The normalized, validated path
    """
    if not path or path.strip() == "":
        raise ValidationError.missing_field("path")

    path = path.strip()

    # Normalize the path to resolve any . or .. components
    normalized = os.path.normpath(path)

    # Check for directory traversal attempts
    if ".." in normalized:
        raise ValidationError(
            user_message="The path contains invalid directory traversal sequences",
            beginner_help="Paths cannot contain '..' to navigate to parent directories",
            common_fixes=[
                "Remove any '..' from the path",
                "Use a direct path without relative navigation",
                "Ensure the path stays within the project directory",
            ],
            docs_anchor="validation-errors",
            technical_message=f"Path traversal detected in: {path}",
        )

    # If base_dir provided, verify the resolved path stays within it
    if base_dir:
        base_dir = os.path.realpath(base_dir)
        # For relative paths, join with base_dir first
        if not os.path.isabs(normalized):
            full_path = os.path.realpath(os.path.join(base_dir, normalized))
        else:
            full_path = os.path.realpath(normalized)

        if not full_path.startswith(base_dir):
            raise ValidationError(
                user_message="The path points outside the allowed directory",
                beginner_help="All paths must stay within the project directory for security",
                common_fixes=[
                    "Ensure the path is within your dbt project",
                    "Use relative paths from the project root",
                    "Remove any directory navigation that goes outside the project",
                ],
                docs_anchor="validation-errors",
                technical_message=f"Path '{path}' resolves outside base_dir '{base_dir}'",
            )

    return normalized


def validate_domain_name(domain: str) -> str:
    """
    Validate a domain/folder name for use in file paths.

    Args:
        domain: The domain name to validate

    Raises:
        ValidationError: If domain contains invalid characters

    Returns:
        str: The validated domain name
    """
    if not domain or domain.strip() == "":
        # Domain is optional, return empty string
        return ""

    domain = domain.strip()

    # Check for path traversal
    if ".." in domain or "/" in domain or "\\" in domain:
        raise ValidationError(
            user_message="The domain name contains invalid characters",
            beginner_help="Domain names cannot contain path separators or '..'",
            common_fixes=[
                "Use only letters, numbers, underscores, and hyphens",
                "Remove any slashes or dots from the domain name",
                "Example: 'sales_data' instead of '../sales_data'",
            ],
            docs_anchor="validation-errors",
            technical_message=f"Invalid domain name: {domain}",
        )

    # Check for valid characters (alphanumeric, underscore, hyphen)
    if not re.match(r"^[a-zA-Z0-9_-]+$", domain):
        raise ValidationError(
            user_message="The domain name contains invalid characters",
            beginner_help="Domain names can only contain letters, numbers, underscores, and hyphens",
            common_fixes=[
                "Remove spaces and special characters",
                "Use underscores instead of spaces",
                "Example: 'my_domain' instead of 'my domain'",
            ],
            docs_anchor="validation-errors",
            technical_message=f"Invalid domain name: {domain}",
        )

    return domain


# File upload validation constants
ALLOWED_EXTENSIONS = {".sql"}
MAX_FILE_SIZE_MB = 2
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


def validate_file_upload(file, filename_override: str | None = None):
    """
    Validate uploaded file for SQL query analysis.

    Args:
        file: FileStorage object from Flask request.files
        filename_override: Optional filename to use for extension validation

    Raises:
        ValidationError: If file is invalid (missing, wrong type, too large)

    Returns:
        FileStorage: The validated file object
    """
    # Check if file exists
    if not file:
        raise ValidationError.missing_field("file")

    # Check if file has a filename
    if not file.filename or file.filename == "":
        raise ValidationError.missing_field("filename")

    # Check file extension
    filename = (filename_override or file.filename).lower()
    file_ext = None
    if "." in filename:
        file_ext = "." + filename.rsplit(".", 1)[1]

    if file_ext not in ALLOWED_EXTENSIONS:
        raise ValidationError.invalid_file_type(filename_override or file.filename, [".sql"])

    # Check file size
    # Read file content to check size
    file.seek(0, 2)  # Seek to end of file
    file_size = file.tell()
    file.seek(0)  # Reset to beginning

    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValidationError.file_too_large(filename_override or file.filename, MAX_FILE_SIZE_MB)

    # Check if file is empty
    if file_size == 0:
        raise ValidationError(
            user_message=f"The file '{filename_override or file.filename}' is empty",
            beginner_help="Your SQL file doesn't contain any content",
            common_fixes=[
                "Make sure you saved your SQL code before uploading",
                "Check that you selected the correct file",
                "Open the file to verify it contains your SQL query",
            ],
            docs_anchor="validation-errors",
        )

    return file


def validate_relative_sql_path(relative_path: str) -> str:
    """
    Validate a relative SQL file path for folder uploads.

    Args:
        relative_path: Relative path provided by the client

    Returns:
        Normalized, validated relative path
    """
    if not relative_path or relative_path.strip() == "":
        raise ValidationError.missing_field("path")

    normalized = validate_safe_path(relative_path)

    if os.path.isabs(normalized) or normalized.startswith("~"):
        raise ValidationError(
            user_message="The file path must be relative",
            beginner_help="Folder uploads only allow relative paths inside the upload directory",
            common_fixes=[
                "Remove any leading slashes",
                "Select a folder instead of an absolute file path",
                "Try uploading the folder again",
            ],
            docs_anchor="validation-errors",
            technical_message=f"Absolute path detected: {relative_path}",
        )

    file_ext = "." + normalized.rsplit(".", 1)[-1].lower() if "." in normalized else ""
    if file_ext not in ALLOWED_EXTENSIONS:
        raise ValidationError.invalid_file_type(relative_path, [".sql"])

    return normalized


def validate_schema_name(schema_name):
    """
    Validate BigQuery schema/dataset name format.

    Args:
        schema_name: String to validate

    Raises:
        ValidationError: If schema name is invalid

    Returns:
        str: The validated schema name
    """
    # Check if schema name exists
    if not schema_name or schema_name.strip() == "":
        raise ValidationError.missing_field("schema name")

    schema_name = schema_name.strip()

    # Check length (BigQuery max is 1024, but we use 100 for practicality)
    if len(schema_name) > 100:
        raise ValidationError(
            user_message="The schema name is too long",
            beginner_help="Schema names should be concise and descriptive",
            common_fixes=["Shorten the schema name to 100 characters or less", "Use abbreviations where appropriate"],
            docs_anchor="configuration-errors",
            technical_message=f"Schema name length: {len(schema_name)} (max: 100)",
        )

    # Check for valid characters (alphanumeric and underscore)
    if not re.match(r"^[a-zA-Z0-9_]+$", schema_name):
        raise ValidationError(
            user_message="The schema name contains invalid characters",
            beginner_help="Schema names can only contain letters, numbers, and underscores",
            common_fixes=[
                "Remove spaces and special characters",
                "Use underscores instead of spaces",
                "Example: 'sales_data' instead of 'sales-data' or 'sales data'",
            ],
            docs_anchor="configuration-errors",
            technical_message=f"Invalid schema name: {schema_name}",
        )

    return schema_name


def validate_materialization_type(materialization):
    """
    Validate dbt materialization type.

    Args:
        materialization: String to validate

    Raises:
        ValidationError: If materialization type is invalid

    Returns:
        str: The validated materialization type
    """
    VALID_MATERIALIZATIONS = {"table", "view", "incremental", "ephemeral"}

    if not materialization or materialization.strip() == "":
        raise ValidationError.missing_field("materialization type")

    materialization = materialization.strip().lower()

    if materialization not in VALID_MATERIALIZATIONS:
        raise ValidationError(
            user_message=f"'{materialization}' is not a valid materialization type",
            beginner_help="dbt supports four materialization types: table, view, incremental, and ephemeral",
            common_fixes=[
                "Choose one of: table, view, incremental, ephemeral",
                "Use 'table' for permanent tables that rebuild completely",
                "Use 'view' for virtual tables that don't store data",
                "Use 'incremental' for large tables that update with new data only",
            ],
            docs_anchor="configuration-errors",
            technical_message=f"Invalid materialization: {materialization}. Valid: {VALID_MATERIALIZATIONS}",
        )

    return materialization


def validate_tags(tags):
    """
    Validate dbt tags list.

    Args:
        tags: List of strings or comma-separated string

    Raises:
        ValidationError: If tags are invalid

    Returns:
        list: List of validated tag strings
    """
    # Convert string to list if needed
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    if not tags or len(tags) == 0:
        # Tags are optional, return empty list
        return []

    validated_tags = []
    for tag in tags:
        tag = str(tag).strip()

        # Check length
        if len(tag) > 50:
            raise ValidationError(
                user_message=f"The tag '{tag[:20]}...' is too long",
                beginner_help="Tags should be short labels (50 characters or less)",
                common_fixes=["Shorten the tag name", "Use abbreviations", "Split into multiple shorter tags"],
                docs_anchor="configuration-errors",
                technical_message=f"Tag length: {len(tag)} (max: 50)",
            )

        # Check for valid characters (alphanumeric, underscore, hyphen)
        if not re.match(r"^[a-zA-Z0-9_-]+$", tag):
            raise ValidationError(
                user_message=f"The tag '{tag}' contains invalid characters",
                beginner_help="Tags can only contain letters, numbers, underscores, and hyphens",
                common_fixes=[
                    "Remove spaces and special characters",
                    "Use underscores or hyphens instead of spaces",
                    "Example: 'sales_report' instead of 'sales report'",
                ],
                docs_anchor="configuration-errors",
                technical_message=f"Invalid tag: {tag}",
            )

        validated_tags.append(tag)

    return validated_tags
