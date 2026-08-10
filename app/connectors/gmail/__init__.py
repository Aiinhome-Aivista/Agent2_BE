"""Gmail/IMAP connector."""

from app.connectors.base.connector import ConnectorMeta

META = ConnectorMeta(
    provider="gmail",
    display_name="Email (IMAP)",
    description="Fetch emails via IMAP and convert them into incidents.",
    auth_type="basic",
    docs_url="https://support.google.com/mail/answer/185833",
    icon="mail",
    capabilities=["inbound", "polling"],
    required_config=["email", "app_password", "imap_server"],
)
