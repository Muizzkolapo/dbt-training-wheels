"""
Custom exception hierarchy for beginner-friendly error handling.
Each exception maps to specific user guidance.
"""


class DbtTrainingWheelsException(Exception):
    """Base exception with user-friendly context."""

    def __init__(self, user_message, beginner_help, common_fixes, docs_anchor, technical_message=None, details=None):
        self.user_message = user_message
        self.beginner_help = beginner_help
        self.common_fixes = common_fixes
        self.docs_anchor = docs_anchor
        self.technical_message = technical_message or str(self)
        # Machine-readable context for the frontend to act on, e.g. a recoverable
        # conflict it can offer to resolve rather than just displaying
        self.details = details or {}
        super().__init__(technical_message)

    @property
    def category(self):
        """Override in subclasses."""
        return "unknown"

    @property
    def code(self):
        """Error code derived from class name."""
        return self.__class__.__name__.upper()


class ValidationError(DbtTrainingWheelsException):
    """User input validation failures."""

    category = "validation"

    @classmethod
    def missing_field(cls, field_name):
        return cls(
            user_message=f"We need you to provide a {field_name}",
            beginner_help="This field is required for the operation to work",
            common_fixes=[
                f"Make sure you've filled in the {field_name} field",
                "Check that the form was fully completed before submitting",
            ],
            docs_anchor="validation-errors",
        )

    @classmethod
    def invalid_file_type(cls, filename, allowed_types):
        return cls(
            user_message=f"The file '{filename}' isn't a supported type",
            beginner_help="We can only process SQL files (.sql extension)",
            common_fixes=[
                f"Make sure your file ends with: {', '.join(allowed_types)}",
                "Check that you selected the correct file",
                "Rename your file to have the .sql extension",
            ],
            docs_anchor="file-upload-errors",
        )

    @classmethod
    def file_too_large(cls, filename, max_size_mb):
        return cls(
            user_message=f"The file '{filename}' is too large",
            beginner_help=f"Files must be smaller than {max_size_mb}MB",
            common_fixes=[
                "Try splitting your SQL into multiple smaller files",
                "Remove unnecessary comments or whitespace",
                "Check that you selected the correct file",
            ],
            docs_anchor="file-upload-errors",
        )

    @classmethod
    def invalid_query_name(cls, query_name):
        return cls(
            user_message="The query name contains invalid characters",
            beginner_help="Query names can only use letters, numbers, underscores (_), and hyphens (-)",
            common_fixes=[
                "Remove spaces and special characters from the name",
                "Use underscores instead of spaces (e.g., 'sales_report' instead of 'sales report')",
                "Only use letters, numbers, _ and -",
            ],
            docs_anchor="validation-errors",
            technical_message=f"Invalid query name: {query_name}",
        )


class SQLParseError(DbtTrainingWheelsException):
    """SQL parsing failures."""

    category = "parsing"

    @classmethod
    def syntax_error(cls, line_num=None, details=None):
        location = f" near line {line_num}" if line_num else ""
        return cls(
            user_message=f"We had trouble understanding your SQL query{location}",
            beginner_help="This usually means there's a syntax issue in your SQL code",
            common_fixes=[
                "Check for unclosed quotes or parentheses",
                "Ensure table names follow: project.dataset.table format",
                "Look for missing commas between column names",
                "Verify all keywords are spelled correctly",
            ],
            docs_anchor="sql-parsing-errors",
            technical_message=details,
        )

    @classmethod
    def unsupported_syntax(cls, feature):
        return cls(
            user_message=f"Your SQL uses '{feature}' which we don't support yet",
            beginner_help="Our parser works with standard BigQuery SQL syntax",
            common_fixes=[
                "Try rewriting the query using standard SQL",
                "Check the documentation for supported SQL features",
                "Break complex queries into simpler steps",
            ],
            docs_anchor="sql-parsing-errors",
            technical_message=f"Unsupported feature: {feature}",
        )


class FileSystemError(DbtTrainingWheelsException):
    """File operations failures."""

    category = "filesystem"

    @classmethod
    def file_not_found(cls, filepath):
        return cls(
            user_message="We couldn't find the file you're looking for",
            beginner_help="The file might have been deleted or moved",
            common_fixes=[
                "Try uploading your SQL files again",
                "Check that you completed the 'Upload Files' step",
                "Refresh the page and start from the beginning",
            ],
            docs_anchor="file-errors",
            technical_message=f"File not found: {filepath}",
        )

    @classmethod
    def permission_denied(cls, filepath):
        return cls(
            user_message="We don't have permission to access this file",
            beginner_help="This is a server configuration issue",
            common_fixes=[
                "Contact your system administrator",
                "Try uploading the file again",
                "Check available disk space",
            ],
            docs_anchor="file-errors",
            technical_message=f"Permission denied: {filepath}",
        )

    @classmethod
    def file_already_exists(cls, filename):
        return cls(
            user_message=f"A file named '{filename}' already exists",
            beginner_help="Re-uploading will replace the existing file",
            common_fixes=[
                "Confirm the overwrite to replace it",
                "Rename your file to something unique",
                "Delete the existing file first",
            ],
            docs_anchor="file-errors",
            technical_message=f"File already exists: {filename}",
            # Lets the frontend offer an overwrite instead of a dead-end error
            details={"conflicts": [filename], "can_overwrite": True},
        )


class ConfigurationError(DbtTrainingWheelsException):
    """Configuration and setup errors."""

    category = "config"

    @classmethod
    def missing_project_name(cls):
        return cls(
            user_message="We need you to set a project name",
            beginner_help="The project name is used to organize your dbt files",
            common_fixes=[
                "Go to 'Configure Settings' and enter a project name",
                "Use a name that describes your project (e.g., 'sales_analytics')",
                "Avoid spaces or special characters in the name",
            ],
            docs_anchor="configuration-errors",
        )

    @classmethod
    def invalid_config_format(cls, field_name):
        return cls(
            user_message=f"The {field_name} configuration has an invalid format",
            beginner_help="Configuration values must follow specific formats",
            common_fixes=[
                f"Check the {field_name} field for typos or invalid characters",
                "Refer to the examples in the configuration guide",
                "Reset to default settings and try again",
            ],
            docs_anchor="configuration-errors",
            technical_message=f"Invalid config format for field: {field_name}",
        )


class AnalysisError(DbtTrainingWheelsException):
    """Query analysis failures."""

    category = "analysis"

    @classmethod
    def table_extraction_failed(cls):
        return cls(
            user_message="We couldn't identify the tables in your query",
            beginner_help="This might be due to complex query structure or non-standard syntax",
            common_fixes=[
                "Ensure table names use the full format: project.dataset.table",
                "Check that your FROM and JOIN clauses are clearly written",
                "Simplify complex subqueries if possible",
            ],
            docs_anchor="analysis-errors",
        )

    @classmethod
    def duplicate_table_names(cls, conflicts):
        """Two different tables in one script share a short name.

        Args:
            conflicts: Dict mapping each short name to the qualified names claiming it
        """
        details = "; ".join(
            f"'{short}' is written as {' and '.join(fulls)}" for short, fulls in sorted(conflicts.items())
        )
        return cls(
            user_message=(f"Two different tables in this script share the same name: {', '.join(sorted(conflicts))}"),
            beginner_help=(
                "Models are named after the table's short name, so two tables that differ only by "
                "project or dataset would collapse into one model built from the wrong SQL"
            ),
            common_fixes=[
                "Rename one of the tables so the short names are unique",
                "Move the statements into separate subfolders and re-upload - each subfolder becomes its own query",
                "Split the script into separate uploads",
            ],
            docs_anchor="analysis-errors",
            technical_message=f"Duplicate short table names: {details}",
        )

    @classmethod
    def recreated_tables(cls, recreated):
        """The same table is built by more than one CREATE statement.

        Args:
            recreated: Dict mapping qualified table name to how many times it's created
        """
        names = sorted(recreated)
        details = "; ".join(f"'{name}' is created {recreated[name]} times" for name in names)
        short_names = [name.split(".")[-1] for name in names]
        return cls(
            user_message=f"This script builds the same table more than once: {', '.join(short_names)}",
            beginner_help=(
                "Only one of those statements can define the model, so the other one's logic "
                "would be silently dropped. This usually means two uploaded files build the same table"
            ),
            common_fixes=[
                "Delete or rename whichever version is out of date",
                "Keep one CREATE and change the other to INSERT INTO if you meant to append",
                "Upload the files separately if they're genuinely different models",
            ],
            docs_anchor="analysis-errors",
            technical_message=f"Tables created more than once: {details}",
        )

    @classmethod
    def no_tables_found(cls):
        return cls(
            user_message="We didn't find any tables in your SQL query",
            beginner_help="Your query needs to reference at least one table",
            common_fixes=[
                "Make sure your query has FROM or JOIN clauses",
                "Check that table names are spelled correctly",
                "Verify this is a complete SQL query, not just a fragment",
            ],
            docs_anchor="analysis-errors",
        )

    @classmethod
    def analysis_timeout(cls):
        return cls(
            user_message="The analysis took too long and was stopped",
            beginner_help="Your query might be too complex to analyze quickly",
            common_fixes=[
                "Try breaking the query into smaller parts",
                "Simplify complex subqueries or CTEs",
                "Remove unnecessary nested queries",
            ],
            docs_anchor="analysis-errors",
        )
