"""Upload API routes for DBT Training Wheels.

Endpoints for file upload and listing.
"""

import logging
import os

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from dbt_training_wheels.config import SQL_DIRECTORY
from dbt_training_wheels.exceptions import FileSystemError, ValidationError
from dbt_training_wheels.utils import handle_route_errors, validate_file_upload, validate_relative_sql_path
from dbt_training_wheels.utils.sql_parser import find_conflicting_table_basenames, find_recreated_tables

logger = logging.getLogger(__name__)

upload_bp = Blueprint("upload", __name__)


@upload_bp.route("/upload", methods=["POST"])
@handle_route_errors
def upload_sql_file():
    """
    Upload a SQL file to the source_sql_file directory.

    Query params:
        overwrite: If 'true', allows overwriting existing files

    Returns:
        JSON response with upload status and file info
    """
    # Validate file upload
    if "file" not in request.files:
        raise ValidationError.missing_field("file")

    file = request.files["file"]

    # Check if overwrite is allowed
    allow_overwrite = request.args.get("overwrite", "false").lower() == "true"

    # Validate file using validator (checks existence, extension, size)
    validate_file_upload(file)

    if not file.filename:
        raise ValidationError(
            user_message="File must have a filename",
            beginner_help="The uploaded file doesn't have a name. Please check the file and try again.",
            common_fixes=["Ensure the file has a name before uploading"],
            docs_anchor="file-upload-errors",
        )

    filename = secure_filename(file.filename)

    # Ensure directory exists
    os.makedirs(SQL_DIRECTORY, exist_ok=True)

    filepath = os.path.join(SQL_DIRECTORY, filename)

    # Check if file already exists (unless overwrite is allowed)
    if os.path.exists(filepath) and not allow_overwrite:
        raise FileSystemError.file_already_exists(filename)

    # Save file
    try:
        file.save(filepath)
    except PermissionError as err:
        raise FileSystemError.permission_denied(filepath) from err
    except Exception as e:
        raise FileSystemError(
            user_message="We couldn't save your file",
            beginner_help="This might be a server storage issue",
            common_fixes=[
                "Try uploading the file again",
                "Check available disk space",
                "Contact support if the problem persists",
            ],
            docs_anchor="file-errors",
            technical_message=f"Failed to save file: {str(e)}",
        ) from e

    return jsonify(
        {
            "success": True,
            "filename": filename,
            "message": f'File "{filename}" uploaded successfully',
            "overwritten": allow_overwrite and os.path.exists(filepath),
        }
    )


def _read_sql_upload(file, normalized_path: str) -> str:
    """Read an uploaded SQL file as UTF-8, with beginner-friendly errors."""
    try:
        file.seek(0)
        return file.read().decode("utf-8").rstrip()
    except UnicodeDecodeError as err:
        raise ValidationError(
            user_message=f"File '{os.path.basename(normalized_path)}' contains invalid characters",
            beginner_help="SQL files must be UTF-8 encoded text files",
            common_fixes=[
                "Ensure the file is saved as UTF-8",
                "Check for binary content in the file",
                "Re-save the file in your editor as UTF-8",
            ],
            docs_anchor="file-encoding-errors",
        ) from err
    except Exception as e:
        raise FileSystemError(
            user_message=f"We couldn't read the file '{os.path.basename(normalized_path)}'",
            beginner_help="This might be a file access issue",
            common_fixes=[
                "Ensure the file is not corrupted",
                "Try uploading the folder again",
            ],
            docs_anchor="file-errors",
            technical_message=f"Failed to read file {normalized_path}: {str(e)}",
        ) from e


def _merge_sql_contents(contents: list[str]) -> str:
    """Join SQL file contents, terminating each with a semicolon if it lacks one."""
    parts = [content if content.rstrip().endswith(";") else content + ";" for content in contents]
    return "\n\n".join(parts)


def _merged_target_for_group(directory: str) -> str:
    """Map a subfolder to the merged file it produces.

    'churn/customer' -> 'churn/customer.sql', 'churn' -> 'churn.sql'. Files with no
    folder segment at all (loose multi-file drops) fall back to 'merged_folder.sql'.
    """
    return f"{directory}.sql" if directory else "merged_folder.sql"


@upload_bp.route("/upload-folder", methods=["POST"])
@handle_route_errors
def upload_sql_folder():
    """
    Upload a folder of SQL files, producing one merged query per subfolder.

    Files are grouped by the directory they sit in; each group is merged into a single
    script named after that directory ('churn/customer/*.sql' -> 'churn/customer.sql'),
    so every subfolder becomes its own query with its own analysis, mart selection and
    deploy. Files within a group are merged in path order, so numeric prefixes
    (00_, 01_, ...) control statement order.

    Query params:
        overwrite: If 'true', allows overwriting existing merged files

    Returns:
        JSON response with the merged files created
    """
    if "files" not in request.files:
        raise ValidationError.missing_field("files")

    files = request.files.getlist("files")
    paths = request.form.getlist("paths")

    if paths and len(paths) != len(files):
        raise ValidationError(
            user_message="Upload paths are missing or invalid",
            beginner_help="Folder uploads must include a path for each file",
            common_fixes=[
                "Try uploading the folder again",
                "Ensure all files are selected",
                "Use the folder upload option in the UI",
            ],
            docs_anchor="validation-errors",
            technical_message=f"Expected {len(files)} paths, got {len(paths)}",
        )

    allow_overwrite = request.args.get("overwrite", "false").lower() == "true"

    # Validate every file and group by the subfolder it came from
    groups: dict[str, list[tuple[str, object]]] = {}
    for idx, file in enumerate(files):
        relative_path = paths[idx] if paths else file.filename
        if not relative_path:
            raise ValidationError(
                user_message="File must have a filename or path",
                beginner_help="The uploaded file doesn't have a name or path. Please check the file and try again.",
                common_fixes=["Ensure the file has a name before uploading"],
                docs_anchor="file-upload-errors",
            )

        normalized_path = validate_relative_sql_path(relative_path)
        validate_file_upload(file, filename_override=normalized_path)
        groups.setdefault(os.path.dirname(normalized_path), []).append((normalized_path, file))

    # Refuse before writing anything, so a conflict never leaves a half-written upload
    targets = {directory: _merged_target_for_group(directory) for directory in groups}
    existing = sorted(target for target in targets.values() if os.path.exists(os.path.join(SQL_DIRECTORY, target)))
    if existing and not allow_overwrite:
        raise ValidationError(
            user_message=f"{len(existing)} query(ies) from this folder already exist: {', '.join(existing)}",
            beginner_help="This folder (or part of it) has already been uploaded. Re-uploading will replace it.",
            common_fixes=[
                "Confirm the overwrite to replace them",
                "Delete the existing file(s) first",
                "Rename your folder before uploading",
            ],
            docs_anchor="validation-errors",
            technical_message=f"Merged files already exist: {', '.join(existing)}",
            # Lets the frontend offer an overwrite instead of a dead-end error
            details={"conflicts": existing, "can_overwrite": True},
        )

    # Read and merge everything BEFORE writing anything, so a rejection in one
    # subfolder never leaves the others half-uploaded
    merged: dict[str, tuple[str, int]] = {}  # target -> (merged content, file count)
    for directory in sorted(groups):
        contents = []
        for normalized_path, file in sorted(groups[directory], key=lambda entry: entry[0]):
            content = _read_sql_upload(file, normalized_path)
            if content:  # Skip empty files
                contents.append(content)
                logger.info(f"Added file to '{targets[directory]}': {os.path.basename(normalized_path)}")

        if not contents:
            logger.info(f"Skipping '{targets[directory]}' - every file in it was empty")
            continue

        merged_content = _merge_sql_contents(contents)

        # Two different tables sharing a short name in one merged script would
        # collapse into a single model built from the wrong SQL - refuse the upload
        # with the exact names so the user can rename or re-foldering them
        # Two files each rebuilding the same table: extraction takes the first CREATE
        # while BigQuery would run the last, so one file's logic is silently lost
        recreated = find_recreated_tables(merged_content)
        if recreated:
            names = sorted(recreated)
            raise ValidationError(
                user_message=(
                    f"'{targets[directory]}' would build the same table more than once: "
                    f"{', '.join(name.split('.')[-1] for name in names)}"
                ),
                beginner_help=(
                    "Two of these files create the same table, so only one of them could define "
                    "the model and the other's logic would be silently dropped"
                ),
                common_fixes=[
                    "Delete or rename whichever version is out of date",
                    "Change one to INSERT INTO if you meant to append",
                    "Move them into separate subfolders - each becomes its own query",
                ],
                docs_anchor="validation-errors",
                technical_message=(
                    f"Tables created more than once in {targets[directory]}: "
                    + "; ".join(f"{name} x{recreated[name]}" for name in names)
                ),
            )

        conflicts = find_conflicting_table_basenames(merged_content)
        if conflicts:
            details = "; ".join(
                f"'{short}' is written as {' and '.join(fulls)}" for short, fulls in sorted(conflicts.items())
            )
            raise ValidationError(
                user_message=(
                    f"'{targets[directory]}' would contain two different tables with the same "
                    f"name: {', '.join(sorted(conflicts))}"
                ),
                beginner_help=(
                    "Models are named after the table's short name, so two tables that differ "
                    "only by project or dataset would collapse into one model with the wrong SQL"
                ),
                common_fixes=[
                    "Rename one of the tables so the short names are unique",
                    "Move the files into separate subfolders - each subfolder becomes its own query",
                    "Upload the files separately",
                ],
                docs_anchor="validation-errors",
                technical_message=f"Duplicate short table names in {targets[directory]}: {details}",
            )

        merged[targets[directory]] = (merged_content, len(contents))

    os.makedirs(SQL_DIRECTORY, exist_ok=True)

    merged_files: list[str] = []
    total_source_files = 0

    for target, (merged_content, file_count) in merged.items():
        merged_filepath = os.path.join(SQL_DIRECTORY, target)
        try:
            os.makedirs(os.path.dirname(merged_filepath) or SQL_DIRECTORY, exist_ok=True)
            with open(merged_filepath, "w", encoding="utf-8") as f:
                f.write(merged_content)
            logger.info(f"Saved merged file: {target} ({file_count} files)")
        except PermissionError as err:
            raise FileSystemError.permission_denied(merged_filepath) from err
        except Exception as e:
            raise FileSystemError(
                user_message=f"We couldn't save the merged file '{target}'",
                beginner_help="This might be a server storage issue",
                common_fixes=[
                    "Try uploading the folder again",
                    "Check available disk space",
                    "Contact support if the problem persists",
                ],
                docs_anchor="file-errors",
                technical_message=f"Failed to save merged file {target}: {str(e)}",
            ) from e

        merged_files.append(target)
        total_source_files += file_count

    if not merged_files:
        raise ValidationError(
            user_message="No valid SQL content found in uploaded files",
            beginner_help="The folder must contain at least one non-empty SQL file",
            common_fixes=[
                "Ensure SQL files have content",
                "Check that files have .sql extension",
            ],
            docs_anchor="validation-errors",
        )

    return jsonify(
        {
            "success": True,
            "merged_files": merged_files,
            "queries_created": len(merged_files),
            # Kept for backward compatibility with older callers
            "merged_file": merged_files[0],
            "source_files_count": total_source_files,
            "message": f"Merged {total_source_files} SQL file(s) into {len(merged_files)} query(ies)",
        }
    )


@upload_bp.route("/files", methods=["GET"])
def list_sql_files():
    """
    List all SQL files in the source_sql_file directory.

    Returns:
        JSON response with list of files
    """
    if not os.path.exists(SQL_DIRECTORY):
        return jsonify({"files": []})

    files = []
    for root, _dirs, filenames in os.walk(SQL_DIRECTORY):
        for filename in filenames:
            if not filename.endswith(".sql"):
                continue
            full_path = os.path.join(root, filename)
            relative_path = os.path.relpath(full_path, SQL_DIRECTORY)
            files.append(relative_path)

    return jsonify({"files": sorted(files)})


@upload_bp.route("/file-exists/<filename>", methods=["GET"])
def check_file_exists(filename):
    """
    Check if a file with the given name already exists.

    Args:
        filename: Name of the file to check

    Returns:
        JSON response with exists status
    """
    from werkzeug.utils import secure_filename as sec_fn

    safe_filename = sec_fn(filename)
    filepath = os.path.join(SQL_DIRECTORY, safe_filename)
    return jsonify({"exists": os.path.exists(filepath), "filename": safe_filename})


@upload_bp.delete("/delete-query/<int:query_id>")
@handle_route_errors
def delete_query(query_id):
    # Delete by filename (may be nested, e.g. "churn/customer.sql")
    filename = request.args.get("filename")
    if not filename:
        raise ValidationError.missing_field("filename")

    # Rejects traversal and absolute paths - filenames are relative to SQL_DIRECTORY
    safe_relative = validate_relative_sql_path(filename)
    target = os.path.join(SQL_DIRECTORY, safe_relative)
    if not os.path.exists(target):
        raise FileSystemError.file_not_found(target)

    os.remove(target)
    return jsonify({"success": True})
