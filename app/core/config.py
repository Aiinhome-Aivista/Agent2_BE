"""Application configuration loaded from environment variables."""
from functools import lru_cache
from typing import List
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env", override=True)

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised settings — single source of truth for all env-driven values."""

    # Application
    app_name: str = "Intelligent Incident Agent"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 3019
    debug: bool = True
    api_prefix: str = "/api/v1"
    frontend_url: str = "http://localhost:5173"

    # Security
    jwt_secret: str = "change-me-in-production-min-32-chars-long-please"
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 60
    jwt_refresh_expire_days: int = 14
    credential_master_key: str = ""

    # Database
    db_host: str = "72.61.226.68"
    db_port: int = 3306
    db_user: str = "aiinhome"
    db_password: str = "Aiin@2026"
    db_name: str = "incident_agent"
    db_pool_size: int = 10

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Agent behaviour
    agent_auto_remediation_threshold: float = 0.85
    agent_escalation_timeout_minutes: int = 15
    agent_kb_auto_publish: bool = False

    # Connectors & Webhooks
    app_public_base_url: str = "https://122.163.121.176:3019"
    webhook_public_base_url: str = "https://122.163.121.176:3019"

    
    # ServiceNow
    servicenow_oauth_client_id: str = ""
    servicenow_oauth_client_secret: str = ""

    # Salesforce
    salesforce_oauth_client_id: str = ""
    salesforce_oauth_client_secret: str = ""

    # HubSpot
    hubspot_oauth_client_id: str = ""
    hubspot_oauth_client_secret: str = ""

    # Zoho
    zoho_oauth_client_id: str = ""
    zoho_oauth_client_secret: str = ""

    # Mistral LLM
    mistral_mode: str = "Local"
    mistral_api_key: str = "IotlgX9OC7gWRj0WqHuT5xdhT1LNkNne"
    model_name: str = "mistral-small-latest"
    mistral_local_url: str = "http://122.163.121.176:3038"
    mistral_local_model: str = "mistral:latest"
    mistral_enabled: bool = True

    # SMTP (for high-priority escalation emails)
    # SMTP (for high-priority escalation emails)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = "aiinhome62@gmail.com"
    smtp_password: str = "ozhefykfihxxlbas"
    smtp_from_address: str = "aiinhome62@gmail.com"
    smtp_from_name: str = "Incident Agent"
    smtp_use_tls: bool = True

    # If set, escalations always copy this address
    smtp_escalation_cc: str = ""

    # GraphDB (ArangoDB)
    arango_url: str = "http://157.173.221.226:8529"
    arango_user: str = "root"
    arango_password: str = "Aiinhome@2026"
    arango_db: str = "incident_graph"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings — instantiated once per process."""
    return Settings()


settings = get_settings()
