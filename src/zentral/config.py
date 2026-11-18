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
from datetime import date
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, PrivateAttr, SecretStr
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from zentral.services.calendar import CalendarError, WorkCalendar

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

PRIORITIES = frozenset({"low", "normal", "high", "urgent"})

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

    # Set on the copy returned by ``zentral.security.keys.materialise`` once the
    # keys have been unwrapped from the keyring.
    _keys_from_keyring: bool = PrivateAttr(default=False)

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./zentral-dev.db"
    public_base_url: str = "http://localhost:8000"
    allowed_hosts: StrList = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    cors_origins: StrList = Field(default_factory=list)
    static_dir: str | None = None
    ticket_prefix: str = "ZEN"
    # Governance: a ticket cannot be resolved or closed until it names the system it concerns.
    require_system_to_resolve: bool = False
    # Service-level targets in hours, per priority. Omit a priority for no target.
    # Hours are working hours for priorities on the business calendar (below),
    # and plain hours for priorities on 24x7. A system can override them.
    sla_response_hours: Annotated[dict[str, float], NoDecode, BeforeValidator(_mapping)] = Field(
        default_factory=lambda: {"urgent": 1.0, "high": 4.0, "normal": 8.0, "low": 24.0}
    )
    sla_resolve_hours: Annotated[dict[str, float], NoDecode, BeforeValidator(_mapping)] = Field(
        default_factory=lambda: {"urgent": 8.0, "high": 24.0, "normal": 72.0, "low": 168.0}
    )
    # Working hours, e.g. "mon-fri 08:00-17:00". Empty: the clock runs around the clock.
    business_hours: str = Field(default="", max_length=500)
    # IANA time zone the working hours and holidays are in, e.g. "Europe/Copenhagen".
    timezone: str = "UTC"
    # Public holidays (YYYY-MM-DD) on which the business clock stands still.
    holidays: StrList = Field(default_factory=list)
    # Which clock each priority runs on: "business" or "24x7".
    sla_calendar: Annotated[dict[str, str], NoDecode, BeforeValidator(_mapping)] = Field(
        default_factory=lambda: {
            "urgent": "24x7",
            "high": "business",
            "normal": "business",
            "low": "business",
        }
    )
    # Attachments (screenshots, logs, PDFs). Off until a malware scanner is in place.
    attachments_enabled: bool = False
    attachment_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)
    attachments_per_ticket: int = Field(default=20, ge=1, le=100)
    # "clamav": scan with clamd before anyone can open a file. "none": development only.
    attachment_scanner: Literal["clamav", "none"] = "clamav"
    clamav_host: str = "clamav"
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    # Requester portal: people sign in with a one-time e-mail link to follow their
    # requests, reply and rate the help they got. Links and replies to tickets
    # opened in the portal go out through this mail channel (its name).
    portal_enabled: bool = False
    portal_mail_channel: str = ""
    portal_link_minutes: int = Field(default=15, ge=5, le=60)
    portal_session_hours: int = Field(default=8, ge=1, le=72)
    # Sign-in links per e-mail address per hour (beyond that, requests are ignored).
    portal_links_per_hour: int = Field(default=3, ge=1, le=20)
    # Audit archiving: a directory (ideally on write-once storage) the worker writes a
    # signed archive of the previous day's audit events to every day. Empty = off.
    audit_archive_dir: str = ""
    # Remove archived events from the database once they are this many days old
    # (0 = keep them in the database as well). Needs ZENTRAL_AUDIT_ARCHIVE_DIR.
    audit_keep_days: int = Field(default=0, ge=0, le=36500)
    # Warn when this share of a target's time is left (0.25 = the last quarter); 0 = never.
    # When a target is missed, the ticket's team is told as well as its assignee.
    escalation_warn_fraction: float = Field(default=0.25, ge=0, lt=1)
    # Resolved tickets close automatically after this many days without a reply (0 = never).
    auto_close_days: int = Field(default=7, ge=0, le=365)

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

    # --- Key custody ----------------------------------------------------------
    # Where the keys above come from. "env": the four settings above, in plain
    # text (development; production only with ZENTRAL_ALLOW_ENV_KEYS). Any other
    # value names a key provider that unwraps an envelope-encrypted keyring at
    # start-up: azure_keyvault, vault_transit, local_file (not for production),
    # or a plug-in from the "zentral.key_providers" entry-point group.
    key_provider: str = "env"
    # The wrapped keyring: a file (preferred) or the document itself. Neither
    # is secret on its own: it is useless without access to the provider's key.
    keyring_file: str = ""
    keyring: str = ""
    # Production refuses plain-text environment keys unless this is set.
    allow_env_keys: bool = False
    # Azure Key Vault / Managed HSM: https://<vault>.vault.azure.net/keys/<name>
    # (or …managedhsm.azure.net/keys/<name>). Wrapping uses the latest version;
    # unwrapping uses the version recorded in the keyring.
    azure_keyvault_key_id: str = ""
    azure_keyvault_algorithm: str = "RSA-OAEP-256"
    # Client ID of a user-assigned managed identity (empty: system-assigned).
    azure_managed_identity_client_id: str = ""
    # HashiCorp Vault / OpenBao Transit.
    vault_addr: str = ""
    vault_namespace: str = ""
    vault_transit_mount: str = "transit"
    vault_transit_key: str = ""
    # Authentication: a token file kept fresh by Vault Agent, or Kubernetes auth.
    vault_token_file: str = ""
    vault_kubernetes_role: str = ""
    vault_kubernetes_mount: str = "kubernetes"
    vault_kubernetes_token_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"  # noqa: S105 # nosec B105
    # local_file provider: a 32-byte AES key (base64) in a file. Development,
    # tests and offline escrow copies only.
    local_kek_file: str = ""
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

    def work_calendar(self) -> WorkCalendar:
        """The business calendar (cached: settings are immutable once loaded)."""
        return _calendar(self.timezone, self.business_hours, tuple(self.holidays))

    def _calendar_problems(self) -> list[str]:
        problems: list[str] = []
        try:
            self.work_calendar()
        except (CalendarError, ValueError) as exc:
            problems.append(f"ZENTRAL_BUSINESS_HOURS / TIMEZONE / HOLIDAYS: {exc}")
        unknown = set(self.sla_calendar) - PRIORITIES
        if unknown:
            problems.append(f"ZENTRAL_SLA_CALENDAR: unknown priority {sorted(unknown)}")
        clocks = set(self.sla_calendar.values()) - {"business", "24x7"}
        if clocks:
            problems.append(f"ZENTRAL_SLA_CALENDAR: use 'business' or '24x7', not {sorted(clocks)}")
        return problems

    def validate_for_runtime(self) -> None:
        """Fail closed on unsafe configuration."""
        problems: list[str] = []
        if not self.encryption_keys:
            problems.append("ZENTRAL_ENCRYPTION_KEYS must contain at least one Fernet key")
        if len(self.blind_index_key.get_secret_value()) < 32:
            problems.append("ZENTRAL_BLIND_INDEX_KEY must be at least 32 characters")
        if len(self.audit_hmac_key.get_secret_value()) < 32:
            problems.append("ZENTRAL_AUDIT_HMAC_KEY must be at least 32 characters")
        if self.key_provider == "env":
            if self.is_production and not self.allow_env_keys:
                problems.append(
                    "production refuses plain-text keys in the environment: configure a key "
                    "provider (ZENTRAL_KEY_PROVIDER, ZENTRAL_KEYRING_FILE) or, knowingly, set "
                    "ZENTRAL_ALLOW_ENV_KEYS=true"
                )
        elif not self._keys_from_keyring:
            problems.append(
                f"ZENTRAL_KEY_PROVIDER={self.key_provider}: the keyring has not been unwrapped"
            )
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
        for name in ("sla_response_hours", "sla_resolve_hours"):
            targets: dict[str, float] = getattr(self, name)
            unknown = set(targets) - PRIORITIES
            if unknown:
                problems.append(f"ZENTRAL_{name.upper()}: unknown priority {sorted(unknown)}")
            if any(hours <= 0 for hours in targets.values()):
                problems.append(f"ZENTRAL_{name.upper()}: hours must be positive")
        problems.extend(self._calendar_problems())
        if self.is_production and self.attachments_enabled and self.attachment_scanner == "none":
            problems.append("attachments in production need ZENTRAL_ATTACHMENT_SCANNER=clamav")
        if self.portal_enabled and not self.portal_mail_channel:
            problems.append("ZENTRAL_PORTAL_ENABLED needs ZENTRAL_PORTAL_MAIL_CHANNEL")
        if self.audit_keep_days and not self.audit_archive_dir:
            problems.append("ZENTRAL_AUDIT_KEEP_DAYS needs ZENTRAL_AUDIT_ARCHIVE_DIR")
        if problems:
            raise ConfigurationError("Unsafe configuration:\n- " + "\n- ".join(problems))


@lru_cache(maxsize=16)
def _calendar(zone: str, hours: str, holidays: tuple[str, ...]) -> WorkCalendar:
    try:
        days = [date.fromisoformat(d) for d in holidays]
    except ValueError:
        raise CalendarError("holidays must be dates like 2026-12-24") from None
    return WorkCalendar.build(zone, hours, days)


@lru_cache
def get_settings() -> Settings:
    return Settings()
