"""Key custody: envelope-encrypted keys, unwrapped once at start-up.

Zentral uses four kinds of key material: the Fernet field-encryption keys, the
blind-index key, the audit HMAC key and retired audit keys. With
``ZENTRAL_KEY_PROVIDER=env`` they are read from environment variables in plain
text — fine on a laptop, but anyone who can read the process environment or the
deployment's configuration has them.

With any other provider they live in a **keyring**: a JSON document holding the
key material encrypted with AES-256-GCM under a random 256-bit *data key*. The
data key itself is stored only *wrapped* by a key-encryption key (KEK) that
never leaves a KMS or HSM (Azure Key Vault / Managed HSM, HashiCorp Vault
Transit, or a plug-in). At start-up the provider unwraps the data key, the
keyring is decrypted in memory and the result fills the same settings the
``env`` provider reads — the rest of the application does not know the
difference. Neither the keyring nor the wrapped key is useful on its own, so
the keyring can sit in a ConfigMap, a mounted file or a backup.

Rotating the KEK (``zentral keys rewrap``) re-wraps the data key; nothing
stored in the database is re-encrypted. Rotating a field-encryption key still
uses ``zentral keys rotate``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, ClassVar

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from zentral.config import ConfigurationError, Settings

FORMAT = "zentral-keyring/1"
GROUP = "zentral.key_providers"
ENV_PROVIDER = "env"

# Settings that carry key material, and their environment variable names.
KEY_FIELDS = (
    "encryption_keys",
    "blind_index_key",
    "audit_hmac_key",
    "audit_hmac_previous_keys",
)


class KeyCustodyError(ConfigurationError):
    """The keyring or the key provider could not deliver the keys.

    Messages never contain key material."""


# --------------------------------------------------------------------- material
@dataclass(frozen=True, slots=True)
class KeyMaterial:
    encryption_keys: tuple[str, ...]
    blind_index_key: str
    audit_hmac_key: str
    audit_hmac_previous_keys: tuple[str, ...] = ()

    def __repr__(self) -> str:  # never print secrets, even by accident
        return f"KeyMaterial({len(self.encryption_keys)} data key(s), <redacted>)"

    __str__ = __repr__

    @classmethod
    def generate(cls) -> KeyMaterial:
        return cls(
            encryption_keys=(Fernet.generate_key().decode("ascii"),),
            blind_index_key=secrets.token_urlsafe(48),
            audit_hmac_key=secrets.token_urlsafe(48),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> KeyMaterial:
        return cls(
            encryption_keys=tuple(k.get_secret_value() for k in settings.encryption_keys),
            blind_index_key=settings.blind_index_key.get_secret_value(),
            audit_hmac_key=settings.audit_hmac_key.get_secret_value(),
            audit_hmac_previous_keys=tuple(
                k.get_secret_value() for k in settings.audit_hmac_previous_keys
            ),
        )

    def problems(self) -> list[str]:
        problems: list[str] = []
        if not self.encryption_keys:
            problems.append("no field-encryption key")
        for key in self.encryption_keys:
            try:
                Fernet(key.encode())
            except ValueError:
                problems.append("a field-encryption key is not a valid Fernet key")
                break
        if len(self.blind_index_key) < 32:
            problems.append("the blind-index key is shorter than 32 characters")
        if len(self.audit_hmac_key) < 32:
            problems.append("the audit key is shorter than 32 characters")
        return problems

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "encryption_keys": list(self.encryption_keys),
                "blind_index_key": self.blind_index_key,
                "audit_hmac_key": self.audit_hmac_key,
                "audit_hmac_previous_keys": list(self.audit_hmac_previous_keys),
            },
            sort_keys=True,
        ).encode()

    @classmethod
    def from_json(cls, data: bytes) -> KeyMaterial:
        doc = json.loads(data)
        return cls(
            encryption_keys=tuple(doc["encryption_keys"]),
            blind_index_key=doc["blind_index_key"],
            audit_hmac_key=doc["audit_hmac_key"],
            audit_hmac_previous_keys=tuple(doc.get("audit_hmac_previous_keys", ())),
        )

    def with_new_data_key(self) -> KeyMaterial:
        """Prepend a fresh field-encryption key (it encrypts; all still decrypt)."""
        return KeyMaterial(
            (Fernet.generate_key().decode("ascii"), *self.encryption_keys),
            self.blind_index_key,
            self.audit_hmac_key,
            self.audit_hmac_previous_keys,
        )

    def without_old_data_keys(self) -> KeyMaterial:
        return KeyMaterial(
            self.encryption_keys[:1],
            self.blind_index_key,
            self.audit_hmac_key,
            self.audit_hmac_previous_keys,
        )

    def with_new_audit_key(self) -> KeyMaterial:
        """A fresh audit key; the current one is kept for verifying older events."""
        return KeyMaterial(
            self.encryption_keys,
            self.blind_index_key,
            secrets.token_urlsafe(48),
            (self.audit_hmac_key, *self.audit_hmac_previous_keys),
        )


def fingerprint(key: str) -> str:
    """A short, non-reversible identifier for showing which key is which."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


# -------------------------------------------------------------------- providers
@dataclass(frozen=True, slots=True)
class WrappedKey:
    kek: str  # the provider's identifier of the KEK *version* that wrapped it
    value: str  # provider-specific, opaque


class KeyProvider(ABC):
    """Wraps and unwraps a 32-byte data key with a key that stays in a KMS/HSM.

    Plug-ins register a subclass under the ``zentral.key_providers``
    entry-point group; it is constructed with the settings and a synchronous
    HTTP client (explicit timeouts, no redirects)."""

    name: ClassVar[str]
    # False for providers whose KEK is not held by a KMS or HSM.
    production_ready: ClassVar[bool] = True

    def __init__(
        self, settings: Settings, http: httpx.Client, environ: Mapping[str, str] | None = None
    ) -> None:
        self.settings = settings
        self.http = http
        self.environ: Mapping[str, str] = os.environ if environ is None else environ

    @abstractmethod
    def wrap(self, dek: bytes) -> WrappedKey: ...

    @abstractmethod
    def unwrap(self, wrapped: WrappedKey) -> bytes: ...

    def describe(self) -> str:
        return self.name


def _builtin_providers() -> dict[str, type[KeyProvider]]:
    from zentral.security.key_providers.azure import AzureKeyVaultProvider
    from zentral.security.key_providers.local import LocalFileProvider
    from zentral.security.key_providers.vault import VaultTransitProvider

    return {p.name: p for p in (AzureKeyVaultProvider, VaultTransitProvider, LocalFileProvider)}


def provider_class(name: str) -> type[KeyProvider]:
    builtin = _builtin_providers()
    if name in builtin:
        return builtin[name]
    for ep in entry_points(group=GROUP):
        if ep.name == name:
            klass = ep.load()
            if not (isinstance(klass, type) and issubclass(klass, KeyProvider)):
                raise KeyCustodyError(f"entry point {name!r} is not a KeyProvider")
            return klass
    raise KeyCustodyError(f"unknown key provider {name!r}")


def load_provider(
    settings: Settings,
    http: httpx.Client,
    *,
    name: str | None = None,
    environ: Mapping[str, str] | None = None,
    for_escrow: bool = False,
) -> KeyProvider:
    """``for_escrow``: the provider only *wraps* a copy (``keys rewrap
    --to-provider``), so a non-KMS provider is acceptable even in production."""
    name = name or settings.key_provider
    if name == ENV_PROVIDER:
        raise KeyCustodyError("ZENTRAL_KEY_PROVIDER=env has no keyring: choose a key provider")
    klass = provider_class(name)
    if settings.is_production and not klass.production_ready and not for_escrow:
        raise KeyCustodyError(f"the {name} key provider is not allowed in production")
    return klass(settings, http, environ)


@contextmanager
def provider_http(http: httpx.Client | None = None) -> Iterator[httpx.Client]:
    if http is not None:
        yield http
        return
    with httpx.Client(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=False,
        headers={"User-Agent": "zentral"},
    ) as client:
        yield client


# ---------------------------------------------------------------------- keyring
def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def _aad(doc: Mapping[str, Any]) -> bytes:
    # Binds the header to the ciphertext: swapping provider, KEK or wrapped key fails.
    header = {k: doc[k] for k in ("format", "provider", "kek", "wrapped_key", "created")}
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode()


def seal(material: KeyMaterial, provider: KeyProvider) -> dict[str, Any]:
    """Encrypt ``material`` into a new keyring document under a fresh data key."""
    problems = material.problems()
    if problems:
        raise KeyCustodyError("refusing to seal invalid keys: " + "; ".join(problems))
    dek = bytearray(AESGCM.generate_key(bit_length=256))
    try:
        wrapped = provider.wrap(bytes(dek))
        doc: dict[str, Any] = {
            "format": FORMAT,
            "provider": provider.name,
            "kek": wrapped.kek,
            "wrapped_key": wrapped.value,
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(bytes(dek)).encrypt(nonce, material.to_json(), _aad(doc))
    finally:
        dek[:] = bytes(len(dek))  # best effort: Python may hold other copies
    doc["nonce"] = _b64(nonce)
    doc["ciphertext"] = _b64(ciphertext)
    return doc


def open_keyring(doc: Mapping[str, Any], provider: KeyProvider) -> KeyMaterial:
    if doc.get("format") != FORMAT:
        raise KeyCustodyError(f"not a Zentral keyring (expected format {FORMAT})")
    if doc.get("provider") != provider.name:
        raise KeyCustodyError(
            f"the keyring was wrapped by {doc.get('provider')!r}, "
            f"but ZENTRAL_KEY_PROVIDER is {provider.name!r}"
        )
    try:
        wrapped = WrappedKey(kek=str(doc["kek"]), value=str(doc["wrapped_key"]))
        nonce, ciphertext = _unb64(doc["nonce"]), _unb64(doc["ciphertext"])
        aad = _aad(doc)
    except (KeyError, ValueError, TypeError) as exc:
        raise KeyCustodyError("the keyring is incomplete or damaged") from exc
    dek = bytearray(provider.unwrap(wrapped))
    try:
        plaintext = AESGCM(bytes(dek)).decrypt(nonce, ciphertext, aad)
    except (InvalidTag, ValueError) as exc:
        raise KeyCustodyError("the keyring failed its integrity check (tampered?)") from exc
    finally:
        dek[:] = bytes(len(dek))
    material = KeyMaterial.from_json(plaintext)
    problems = material.problems()
    if problems:
        raise KeyCustodyError("the keyring holds invalid keys: " + "; ".join(problems))
    return material


@dataclass(frozen=True, slots=True)
class KeyringSource:
    document: dict[str, Any]
    path: Path | None = field(default=None)


def read_keyring(settings: Settings) -> KeyringSource:
    if settings.keyring_file and settings.keyring:
        raise KeyCustodyError("set ZENTRAL_KEYRING_FILE or ZENTRAL_KEYRING, not both")
    if settings.keyring_file:
        path = Path(settings.keyring_file)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise KeyCustodyError(
                f"cannot read ZENTRAL_KEYRING_FILE ({type(exc).__name__})"
            ) from exc
    elif settings.keyring:
        path, text = None, settings.keyring
    else:
        raise KeyCustodyError(
            f"ZENTRAL_KEY_PROVIDER={settings.key_provider} needs ZENTRAL_KEYRING_FILE "
            "(create one with `zentral keys init` or `zentral keys wrap`)"
        )
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise KeyCustodyError("the keyring is not valid JSON") from exc
    if not isinstance(document, dict):
        raise KeyCustodyError("the keyring is not a JSON object")
    return KeyringSource(document, path)


def write_keyring(path: Path, document: Mapping[str, Any], *, backup: bool = True) -> Path | None:
    """Write atomically with mode 600. An existing file is kept as a dated backup:
    it is still wrapped, and needed to read backups taken under retired keys."""
    kept: Path | None = None
    if path.exists() and backup:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        kept = path.with_name(f"{path.name}.{stamp}.bak")
        shutil.copy2(path, kept)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return kept


def _has_env_keys(settings: Settings) -> list[str]:
    present = []
    for name in KEY_FIELDS:
        value = getattr(settings, name)
        if (isinstance(value, SecretStr) and value.get_secret_value()) or (
            isinstance(value, list) and value
        ):
            present.append(f"ZENTRAL_{name.upper()}")
    return present


def apply(settings: Settings, material: KeyMaterial) -> Settings:
    resolved = settings.model_copy(
        update={
            "encryption_keys": [SecretStr(k) for k in material.encryption_keys],
            "blind_index_key": SecretStr(material.blind_index_key),
            "audit_hmac_key": SecretStr(material.audit_hmac_key),
            "audit_hmac_previous_keys": [SecretStr(k) for k in material.audit_hmac_previous_keys],
        }
    )
    resolved._keys_from_keyring = True
    return resolved


def materialise(
    settings: Settings,
    *,
    http: httpx.Client | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Return settings with the key material filled in from the keyring.

    With the ``env`` provider the settings are returned unchanged. Otherwise
    the keys must *not* also be set in the environment: that would defeat the
    point, and it would be ambiguous which ones are in use."""
    if settings.key_provider == ENV_PROVIDER or settings._keys_from_keyring:
        return settings
    present = _has_env_keys(settings)
    if present:
        raise KeyCustodyError(
            f"ZENTRAL_KEY_PROVIDER={settings.key_provider}, but keys are also set in the "
            f"environment: remove {', '.join(present)}"
        )
    source = read_keyring(settings)
    with provider_http(http) as client:
        provider = load_provider(settings, client, environ=environ)
        material = open_keyring(source.document, provider)
    return apply(settings, material)


def custody_summary(settings: Settings) -> tuple[str, str]:
    """``(status, detail)`` for ``zentral doctor``. Reads only the keyring header."""
    if settings.key_provider == ENV_PROVIDER:
        if settings.is_production:
            return "warn", (
                "plain-text keys from the environment (ZENTRAL_ALLOW_ENV_KEYS): a compromised "
                "host or leaked configuration exposes all data — use a key provider"
            )
        return "ok", "plain-text keys from the environment (development)"
    doc = read_keyring(settings).document
    return "ok", (
        f"{doc.get('provider')}: data key wrapped by {doc.get('kek')}, "
        f"keyring sealed {doc.get('created')}"
    )
