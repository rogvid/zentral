"""Field-level encryption and blind indexes.

Personal data (message bodies, subjects, contact details, connector secrets) is
encrypted in the application before it reaches the database, so a database
dump, backup or replica on its own does not disclose content.

* Encryption: Fernet (AES-128-CBC + HMAC-SHA256, authenticated) via
  ``MultiFernet`` so keys can be rotated without downtime.
* Blind index: HMAC-SHA256 over a normalised identifier, allowing equality
  lookup ("find the contact with this e-mail") without storing it in clear.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from zentral.config import Settings


class CryptoError(RuntimeError):
    pass


class FieldCipher:
    def __init__(self, keys: list[str], blind_index_key: str) -> None:
        if not keys:
            raise CryptoError("no encryption keys configured")
        try:
            self._fernet = MultiFernet([Fernet(k.encode()) for k in keys])
        except ValueError as exc:
            raise CryptoError("invalid Fernet key in ZENTRAL_ENCRYPTION_KEYS") from exc
        if not blind_index_key:
            raise CryptoError("no blind index key configured")
        self._index_key = blind_index_key.encode()

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode()
        except InvalidToken as exc:
            raise CryptoError("ciphertext could not be decrypted with any configured key") from exc

    def encrypt_bytes(self, data: bytes) -> str:
        return self._fernet.encrypt(data).decode("ascii")

    def decrypt_bytes(self, token: str) -> bytes:
        try:
            return self._fernet.decrypt(token.encode("ascii"))
        except InvalidToken as exc:
            raise CryptoError("ciphertext could not be decrypted with any configured key") from exc

    def rotate(self, token: str) -> str:
        """Re-encrypt ``token`` under the current primary key."""
        return self._fernet.rotate(token.encode("ascii")).decode("ascii")

    def blind_index(self, namespace: str, value: str) -> str:
        normalised = f"{namespace}:{value.strip().casefold()}".encode()
        return hmac.new(self._index_key, normalised, hashlib.sha256).hexdigest()


_cipher: FieldCipher | None = None


def configure(settings: Settings) -> FieldCipher:
    global _cipher
    _cipher = FieldCipher(
        [k.get_secret_value() for k in settings.encryption_keys],
        settings.blind_index_key.get_secret_value(),
    )
    return _cipher


def get_cipher() -> FieldCipher:
    if _cipher is None:
        raise CryptoError("field cipher is not configured")
    return _cipher


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


# --- opaque bearer tokens for ingest sources ---------------------------------

INGEST_TOKEN_PREFIX = "zis_"  # noqa: S105 - a prefix, not a secret # nosec B105


def new_ingest_token() -> tuple[str, str]:
    """Return ``(token, sha256_hex)``. Only the hash is ever stored."""
    token = INGEST_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    # 256 bits of entropy: a plain hash is sufficient, no slow KDF required.
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(a: str | bytes, b: str | bytes) -> bool:
    a_bytes = a.encode() if isinstance(a, str) else a
    b_bytes = b.encode() if isinstance(b, str) else b
    return hmac.compare_digest(a_bytes, b_bytes)
