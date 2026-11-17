"""Application configuration.

All configuration comes from environment variables prefixed with ``ZENTRAL_``
(or a ``.env`` file in development). Secrets are typed as ``SecretStr`` so they
never appear in ``repr()`` output or logs.

``Settings.validate_for_runtime`` enforces the production safety invariants:
the process refuses to start if a production deployment is misconfigured in a
way that would weaken security (fail closed, not open).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_CATEGORIES = (
    "incident",
    "service_request",
    "access",
    "hardware",
    "software",
    "network",
    "security",
    "billing",
    "other",
)

BUILTIN_CONNECTORS = ("webhook", "teams", "email_graph")

# Providers that send data outside the organisation's own infrastructure.
CLOUD_AI_PROVIDERS = frozenset({"anthropic", "claude_cli"})


def _split(value: Any) -> Any:
    """Accept ``a,b,c`` (shell friendly) as well as a JSON array for list settings."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            return json.loads(text)
        return [item.strip() for item in text.split(",") if item.strip()]
    return value


def _mapping(value: Any) -> Any:
    """Accept a JSON object, or ``external=Role,other=Role`` for mapping settings."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            return json.loads(text)
        pairs = (item.split("=", 1) for item in text.split(",") if "=" in item)
        return {k.strip(): v.strip() for k, v in pairs}
    return value


StrList = Annotated[list[str], NoDecode, BeforeValidator(_split)]
SecretList = Annotated[list[SecretStr], NoDecode, BeforeValidator(_split)]


class ConfigurationError(RuntimeError):
    """Raised when the configuration is unsafe or incomplete."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZENTRAL_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./zentral-dev.db"
    public_base_url: str = "http://localhost:8000"
    allowed_hosts: StrList = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    cors_origins: StrList = Field(default_factory=list)
    static_dir: str | None = None
    ticket_prefix: str = "ZEN"
    # Governance: a ticket cannot be resolved or closed until it names the system it concerns.
    require_system_to_resolve: bool = False

    # --- Authentication -------------------------------------------------------
    # entra: Microsoft Entra ID · oidc: any OpenID Connect provider · dev: built-in dev IdP
    auth_mode: Literal["entra", "oidc", "dev"] = "entra"
    # Map IdP role or group values to Zentral roles, e.g.
    # {"servicedesk-admins": "Zentral.Admin", "<entra-group-id>": "Zentral.Agent"}.
    role_map: Annotated[dict[str, str], NoDecode, BeforeValidator(_mapping)] = Field(
        default_factory=dict
    )

    # Microsoft Entra ID
    entra_tenant_id: str = ""
    entra_api_client_id: str = ""
    entra_spa_client_id: str = ""
    entra_authority_host: str = "https://login.microsoftonline.com"
    entra_required_scope: str = "access_as_user"
    # Extra accepted audiences; ``<client-id>`` and ``api://<client-id>`` are always accepted.
    entra_extra_audiences: StrList = Field(default_factory=list)

    # Generic OpenID Connect provider (auth_mode=oidc)
    oidc_issuer: str = ""
    oidc_jwks_uri: str = ""  # optional; discovered from the issuer when empty
    oidc_audiences: StrList = Field(default_factory=list)
    oidc_algorithms: StrList = Field(default_factory=lambda: ["RS256"])
    oidc_client_id: str = ""  # public client used by the web UI
    oidc_scopes: StrList = Field(default_factory=lambda: ["openid", "profile"])
    oidc_required_scope: str = ""
    oidc_subject_claim: str = "sub"
    oidc_scope_claim: str = "scope"
    oidc_roles_claim: str = "roles"
    oidc_groups_claim: str = "groups"
    oidc_name_claim: str = "name"
    oidc_email_claim: str = "email"
    # Extra origins the browser may call during sign-in (if the IdP's token
    # endpoint lives on a different origin than its issuer).
    csp_connect_extra: StrList = Field(default_factory=list)

    # --- Data protection ----------------------------------------------------
    # Fernet keys, newest first. All are tried for decryption; the first encrypts.
    encryption_keys: SecretList = Field(default_factory=list)
    # Key for blind indexes (deterministic lookup of encrypted identifiers).
    blind_index_key: SecretStr = SecretStr("")
    # Key for the tamper-evident audit chain (HMAC-SHA256).
    audit_hmac_key: SecretStr = SecretStr("")
    # Retired audit keys, still accepted when verifying older events.
    audit_hmac_previous_keys: SecretList = Field(default_factory=list)
    # Tickets resolved/closed for longer than this are scrubbed of personal data.
    retention_days: int = Field(default=365, ge=1)

    # --- AI classification --------------------------------------------------
    ai_provider: str = "rules"
    ai_model: str = ""
    ai_base_url: str = ""
    ai_api_key: SecretStr = SecretStr("")
    ai_timeout_seconds: float = Field(default=30.0, gt=0)
    # Pattern-based PII redaction before text leaves the application for the model.
    ai_redact_pii: bool = True
    # Below this confidence the AI result is stored as a suggestion only.
    ai_min_confidence: float = Field(default=0.6, ge=0, le=1)
    categories: StrList = Field(default_factory=lambda: list(DEFAULT_CATEGORIES))

    # --- Connectors ---------------------------------------------------------
    enabled_connectors: StrList = Field(default_factory=lambda: list(BUILTIN_CONNECTORS))
    # Hosts connectors may call. Suffix match on a dot boundary.
    outbound_host_allowlist: StrList = Field(
        default_factory=lambda: [
            "graph.microsoft.com",
            "login.microsoftonline.com",
            "webhook.office.com",
            "logic.azure.com",
            "api.powerplatform.com",
        ]
    )
    poll_interval_seconds: int = Field(default=60, ge=10)

    # --- Abuse protection ---------------------------------------------------
    max_body_bytes: int = Field(default=1_048_576, ge=1024)
    max_message_chars: int = Field(default=100_000, ge=1000)
    rate_limit_per_minute: int = Field(default=300, ge=1)
    hook_rate_limit_per_minute: int = Field(default=120, ge=1)
    # Failed authentications tolerated per client address before it is throttled.
    auth_failure_limit_per_minute: int = Field(default=30, ge=1)

    log_level: str = "INFO"

    # ------------------------------------------------------------------ helpers
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def entra_issuer(self) -> str:
        return f"{self.entra_authority_host}/{self.entra_tenant_id}/v2.0"

    @property
    def entra_jwks_uri(self) -> str:
        return f"{self.entra_authority_host}/{self.entra_tenant_id}/discovery/v2.0/keys"

    @property
    def entra_audiences(self) -> list[str]:
        cid = self.entra_api_client_id
        return [cid, f"api://{cid}", *self.entra_extra_audiences]

    @property
    def issuer(self) -> str:
        """The token issuer for the active authentication mode."""
        if self.auth_mode == "entra":
            return self.entra_issuer
        if self.auth_mode == "oidc":
            return self.oidc_issuer
        return self.public_base_url.rstrip("/") + "/dev-idp"

    @property
    def ai_is_cloud(self) -> bool:
        return self.ai_provider in CLOUD_AI_PROVIDERS

    def validate_for_runtime(self) -> None:
        """Fail closed on unsafe configuration."""
        problems: list[str] = []
        if not self.encryption_keys:
            problems.append("ZENTRAL_ENCRYPTION_KEYS must contain at least one Fernet key")
        if len(self.blind_index_key.get_secret_value()) < 32:
            problems.append("ZENTRAL_BLIND_INDEX_KEY must be at least 32 characters")
        if len(self.audit_hmac_key.get_secret_value()) < 32:
            problems.append("ZENTRAL_AUDIT_HMAC_KEY must be at least 32 characters")
        if self.auth_mode == "entra" and not (self.entra_tenant_id and self.entra_api_client_id):
            problems.append("Entra auth requires ZENTRAL_ENTRA_TENANT_ID and _API_CLIENT_ID")
        if self.auth_mode == "oidc":
            if not (self.oidc_issuer and self.oidc_audiences):
                problems.append("OIDC auth requires ZENTRAL_OIDC_ISSUER and ZENTRAL_OIDC_AUDIENCES")
            if self.is_production and not self.oidc_issuer.startswith("https://"):
                problems.append("production requires an https ZENTRAL_OIDC_ISSUER")
        if self.is_production:
            if self.auth_mode == "dev":
                problems.append("ZENTRAL_AUTH_MODE=dev is not allowed in production")
            if self.database_url.startswith("sqlite"):
                problems.append("production requires PostgreSQL, not SQLite")
            if "*" in self.cors_origins:
                problems.append("wildcard CORS origins are not allowed in production")
            if not self.public_base_url.startswith("https://"):
                problems.append("production requires an https ZENTRAL_PUBLIC_BASE_URL")
            if self.ai_provider == "claude_cli":
                problems.append("the claude_cli AI provider is for local testing only")
            if self.ai_is_cloud and not self.ai_redact_pii:
                problems.append("PII redaction cannot be disabled for cloud AI providers")
        if problems:
            raise ConfigurationError("Unsafe configuration:\n- " + "\n- ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
