"""Connector-specific exception hierarchy."""
from app.core.exceptions import AppException


class ConnectorError(AppException):
    """Base class for any connector-related failure."""

    status_code = 502  # Bad Gateway — the upstream provider failed us
    code = "CONNECTOR_ERROR"


class ConnectorAuthError(ConnectorError):
    """OAuth or credential failure."""

    status_code = 401
    code = "CONNECTOR_AUTH_ERROR"


class ConnectorRateLimitError(ConnectorError):
    """Provider returned 429."""

    status_code = 429
    code = "CONNECTOR_RATE_LIMIT"

    def __init__(self, message: str, retry_after: int = 60) -> None:
        super().__init__(message, {"retry_after": retry_after})
        self.retry_after = retry_after


class ConnectorNotFoundError(ConnectorError):
    """Resource not found in provider."""

    status_code = 404
    code = "CONNECTOR_NOT_FOUND"


class ConnectorConfigError(ConnectorError):
    """Misconfigured connector (missing required field, bad URL, …)."""

    status_code = 400
    code = "CONNECTOR_CONFIG_ERROR"


class WebhookSignatureError(ConnectorError):
    """Webhook arrived with an invalid or missing signature."""

    status_code = 401
    code = "WEBHOOK_SIGNATURE_INVALID"
