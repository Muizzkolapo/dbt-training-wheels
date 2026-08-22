"""
Centralized error handling utilities for consistent error responses.
"""

import logging
import traceback
import uuid
from datetime import datetime
from functools import wraps
from typing import Any

from flask import jsonify

from dbt_training_wheels.exceptions.dbt_training_wheels_exceptions import DbtTrainingWheelsException

logger = logging.getLogger(__name__)


def generate_trace_id():
    """Generate unique trace ID for error tracking."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    return f"err_{timestamp}_{unique_id}"


def format_error_response(exception: Exception, status_code: int = 500, include_trace: bool = True):
    """
    Convert exception to standardized error response.

    Args:
        exception: The exception object
        status_code: HTTP status code (default 500)
        include_trace: Whether to include full traceback (dev mode)

    Returns:
        Flask JSON response with error details
    """
    trace_id = generate_trace_id()

    if isinstance(exception, DbtTrainingWheelsException):
        # Structured exception with user context
        response: dict[str, Any] = {
            "success": False,
            "error": {
                "code": exception.code,
                "category": exception.category,
                "user_message": exception.user_message,
                "beginner_help": exception.beginner_help,
                "common_fixes": exception.common_fixes,
                "docs_link": f"/troubleshooting#{exception.docs_anchor}",
                # Machine-readable context, e.g. a conflict the frontend can offer to resolve
                "details": getattr(exception, "details", {}) or {},
                "technical_details": {
                    "exception_type": exception.__class__.__name__,
                    "message": exception.technical_message,
                    "trace_id": trace_id,
                },
            },
            "timestamp": datetime.now().isoformat(),
        }

        if include_trace:
            response["error"]["technical_details"]["traceback"] = traceback.format_exc()

    else:
        # Generic exception - provide safe defaults
        response = {
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "category": "unknown",
                "user_message": "Something unexpected happened",
                "beginner_help": "This is an internal error that we didn't anticipate",
                "common_fixes": [
                    "Try refreshing the page and attempting the operation again",
                    "Check that all required fields are filled out correctly",
                    "If the problem persists, contact support with this trace ID",
                ],
                "docs_link": "/troubleshooting#general-errors",
                "technical_details": {
                    "exception_type": exception.__class__.__name__,
                    "message": str(exception),
                    "trace_id": trace_id,
                },
            },
            "timestamp": datetime.now().isoformat(),
        }

        if include_trace:
            response["error"]["technical_details"]["traceback"] = traceback.format_exc()

    # Log error for debugging
    logger.error(f"[{trace_id}] {exception.__class__.__name__}: {str(exception)}")

    return jsonify(response), status_code


def handle_route_errors(f):
    """
    Decorator for Flask routes to handle errors consistently.

    Usage:
        @app.route('/api/analyze')
        @handle_route_errors
        def analyze_query():
            # Your route logic
            pass
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except DbtTrainingWheelsException as e:
            # Known errors with user context
            status_map = {"validation": 400, "parsing": 400, "filesystem": 404, "config": 400, "analysis": 422}
            status_code = status_map.get(e.category, 500)
            if e.category == "filesystem":
                message = (e.technical_message or "").lower()
                if "already exists" in message:
                    status_code = 409
                elif "permission denied" in message:
                    status_code = 403
            return format_error_response(e, status_code)
        except Exception as e:
            # Unexpected errors
            return format_error_response(e, 500)

    return decorated_function
