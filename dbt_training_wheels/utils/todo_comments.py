"""Utility for building TODO comment sections in generated dbt models.

This module provides a centralized way to generate consistent TODO and
informational comment sections across all generated models. This ensures:
- Consistent formatting across staging, intermediate, and mart models
- Single source of truth for TODO comment structure
- Easy testing and validation of comment generation
"""

from dataclasses import dataclass


@dataclass
class ScheduledQueryTodo:
    """Info about a scheduled query dependency that needs conversion."""

    project: str
    table: str
    full_ref: str


@dataclass
class DeclareVariable:
    """Info about a DECLARE variable from BigQuery SQL."""

    variable: str
    type: str
    default_value: str | None = None


class TodoCommentBuilder:
    """Builder for generating TODO comment sections in dbt models.

    This class collects TODO items and generates formatted comment sections
    for the top of dbt model files. It handles:
    - Scheduled query conversion TODOs
    - DECLARE variable conversion TODOs
    - Informational notes about auto-converted variables

    Usage:
        builder = TodoCommentBuilder()
        builder.add_scheduled_query_todo(project="my_project", table="my_table", full_ref="...")
        builder.add_remaining_declare_variable(variable="end_date", type="DATE", default_value="...")
        sql = builder.build_comment_section() + original_sql
    """

    # Section divider for visual separation
    DIVIDER = "-- ============================================"

    def __init__(self) -> None:
        """Initialize empty builder."""
        self._scheduled_query_todos: list[ScheduledQueryTodo] = []
        self._remaining_declare_vars: list[DeclareVariable] = []
        self._original_declare_vars: list[DeclareVariable] = []
        self._auto_converted_count: int = 0
        self._is_file_level_vars: bool = False

    def add_scheduled_query_todo(self, project: str, table: str, full_ref: str) -> "TodoCommentBuilder":
        """Add a TODO for a scheduled query dependency.

        Args:
            project: The scheduled query project name
            table: The table display name (dataset.table format)
            full_ref: The full table reference

        Returns:
            Self for method chaining
        """
        self._scheduled_query_todos.append(ScheduledQueryTodo(project=project, table=table, full_ref=full_ref))
        return self

    def add_remaining_declare_variable(
        self, variable: str, var_type: str, default_value: str | None = None
    ) -> "TodoCommentBuilder":
        """Add a DECLARE variable that couldn't be auto-converted.

        Args:
            variable: Variable name
            var_type: Data type (DATE, STRING, etc.)
            default_value: Optional default value expression

        Returns:
            Self for method chaining
        """
        self._remaining_declare_vars.append(
            DeclareVariable(variable=variable, type=var_type, default_value=default_value)
        )
        return self

    def set_original_declare_variables(
        self, variables: list[dict], auto_converted_count: int = 0, is_file_level: bool = False
    ) -> "TodoCommentBuilder":
        """Set the original DECLARE variables for informational notes.

        Args:
            variables: List of variable dicts with 'variable', 'type', 'defaultValue' keys
            auto_converted_count: Number of variables that were auto-converted
            is_file_level: Whether these are file-level variables (passed from parent SQL)

        Returns:
            Self for method chaining
        """
        self._original_declare_vars = [
            DeclareVariable(
                variable=v.get("variable", "unknown"),
                type=v.get("type", "UNKNOWN"),
                default_value=v.get("defaultValue"),
            )
            for v in variables
        ]
        self._auto_converted_count = auto_converted_count
        self._is_file_level_vars = is_file_level
        return self

    def has_todos(self) -> bool:
        """Check if there are any TODO items to generate."""
        return bool(self._scheduled_query_todos or self._remaining_declare_vars)

    def has_informational_notes(self) -> bool:
        """Check if there are informational notes to generate."""
        return bool(self._original_declare_vars) and not self._remaining_declare_vars

    def build_todo_section(self) -> list[str]:
        """Build the TODO LIST section.

        Returns:
            List of comment lines (without trailing newline)
        """
        if not self.has_todos():
            return []

        lines = [
            self.DIVIDER,
            "-- TODO LIST",
            self.DIVIDER,
        ]

        # Add scheduled query TODOs
        if self._scheduled_query_todos:
            lines.extend(
                [
                    "-- The following tables are from scheduled query projects.",
                    "-- You must please convert those scheduled queries to dbt models as well,",
                    "-- then update these source() calls to ref() calls, this would make it easy for you to see links between your tables easier.",
                    "--",
                ]
            )

            for todo in self._scheduled_query_todos:
                lines.append(
                    f"-- TODO: Convert the scheduled query from {todo.project} to a dbt model, "
                    f"then change this source() to a ref() (table: {todo.table})"
                )

        # Add DECLARE variable TODOs
        if self._remaining_declare_vars:
            if self._scheduled_query_todos:
                lines.append("--")  # Separator between sections

            if self._auto_converted_count > 0:
                lines.extend(
                    [
                        f"-- Note: {self._auto_converted_count} DECLARE variable(s) were auto-converted to CTEs.",
                        "-- The following DECLARE variables could NOT be auto-converted.",
                        "-- You'll need to handle these manually (convert to CTEs, config vars, or hardcode values).",
                        "--",
                    ]
                )
            else:
                lines.extend(
                    [
                        "-- The following DECLARE variables are not supported by dbt.",
                        "-- You'll need to handle these manually (convert to CTEs, config vars, or hardcode values).",
                        "--",
                    ]
                )

            for var in self._remaining_declare_vars:
                if var.default_value:
                    lines.append(
                        f"-- TODO: Handle DECLARE variable '{var.variable}' ({var.type}) = {var.default_value}"
                    )
                else:
                    lines.append(f"-- TODO: Handle DECLARE variable '{var.variable}' ({var.type})")

        # Close TODO section
        lines.extend(
            [
                self.DIVIDER,
                "",  # Empty line for spacing
            ]
        )

        return lines

    def build_info_section(self) -> list[str]:
        """Build the INFORMATIONAL NOTE section.

        This is for successfully converted or file-level variables.

        Returns:
            List of comment lines (without trailing newline)
        """
        if not self.has_informational_notes():
            return []

        if self._is_file_level_vars:
            # Variables declared at file level and referenced in this model
            lines = [
                self.DIVIDER,
                "-- INFORMATIONAL NOTE: File-Level Variables",
                self.DIVIDER,
                f"-- This model references {len(self._original_declare_vars)} DECLARE variable(s) from the original SQL file.",
                "-- These variables are not supported by dbt and need to be replaced.",
                "--",
            ]

            # Add details for each variable
            for var in self._original_declare_vars:
                if var.default_value:
                    lines.append(f"--   DECLARE {var.variable} ({var.type}) = {var.default_value}")
                else:
                    lines.append(f"--   DECLARE {var.variable} ({var.type})")

            lines.extend(
                [
                    "--",
                    "-- How to fix:",
                    "-- Option 1: Move to dbt_project.yml vars section",
                    "-- Option 2: Use Jinja at top of model ({% set var = value %})",
                    "-- Option 3: Replace with literal values if they're constants",
                    self.DIVIDER,
                    "",
                ]
            )
        else:
            # All DECLARE variables were successfully auto-converted
            lines = [
                self.DIVIDER,
                "-- INFORMATIONAL NOTE",
                self.DIVIDER,
                f"-- This SQL originally contained {len(self._original_declare_vars)} DECLARE variable(s).",
                "-- They have been automatically converted to CTEs for dbt compatibility.",
                "-- Please verify the logic is correct in the WITH clauses below.",
                self.DIVIDER,
                "",
            ]

        return lines

    def build_comment_section(self) -> str:
        """Build the complete comment section to prepend to SQL.

        This combines TODO section and informational notes as appropriate.

        Returns:
            Complete comment section as a string (with trailing newline)
        """
        lines: list[str] = []

        # Add TODO section first (if any)
        todo_lines = self.build_todo_section()
        if todo_lines:
            lines.extend(todo_lines)

        # Add informational notes (only if no remaining TODOs for variables)
        info_lines = self.build_info_section()
        if info_lines:
            lines.extend(info_lines)

        if not lines:
            return ""

        return "\n".join(lines) + "\n"


def build_todo_comment(
    scheduled_query_todos: list[dict] | None = None,
    remaining_declare_variables: list[dict] | None = None,
    original_declare_variables: list[dict] | None = None,
    auto_converted_count: int = 0,
    is_file_level_vars: bool = False,
) -> str:
    """Convenience function to build a TODO comment section.

    This is a simpler interface for common use cases.

    Args:
        scheduled_query_todos: List of dicts with 'project', 'table', 'full_ref' keys
        remaining_declare_variables: List of variable dicts that couldn't be converted
        original_declare_variables: List of all original variables (for info notes)
        auto_converted_count: Number of variables auto-converted
        is_file_level_vars: Whether variables are file-level

    Returns:
        Complete comment section as string
    """
    builder = TodoCommentBuilder()

    # Add scheduled query TODOs
    if scheduled_query_todos:
        for todo in scheduled_query_todos:
            builder.add_scheduled_query_todo(
                project=todo.get("project", ""),
                table=todo.get("table", ""),
                full_ref=todo.get("full_ref", ""),
            )

    # Add remaining DECLARE variable TODOs
    if remaining_declare_variables:
        for var in remaining_declare_variables:
            builder.add_remaining_declare_variable(
                variable=var.get("variable", "unknown"),
                var_type=var.get("type", "UNKNOWN"),
                default_value=var.get("defaultValue"),
            )

    # Set original variables for info notes
    if original_declare_variables:
        builder.set_original_declare_variables(
            variables=original_declare_variables,
            auto_converted_count=auto_converted_count,
            is_file_level=is_file_level_vars,
        )

    return builder.build_comment_section()
