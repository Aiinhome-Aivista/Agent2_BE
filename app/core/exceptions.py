"""Domain exceptions — caught by global exception handlers in main.py."""
from typing import Any, Dict, Optional


class AppException(Exception):
    """Base application exception."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppException):
    status_code = 404
    code = "NOT_FOUND"


class ValidationError(AppException):
    status_code = 422
    code = "VALIDATION_ERROR"


class UnauthorizedError(AppException):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(AppException):
    status_code = 403
    code = "FORBIDDEN"


class ConflictError(AppException):
    status_code = 409
    code = "CONFLICT"


class AgentExecutionError(AppException):
    status_code = 500
    code = "AGENT_ERROR"
