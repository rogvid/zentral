"""A key-encryption key in a local file.

Not a KMS: whoever can read the file can unwrap the keyring. Use it for
development, tests, and for an *offline escrow copy* of the keyring (a second
keyring wrapped under a key kept on removable media in a safe). Refused in
production as the running provider.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from pathlib import Path
from typing import ClassVar

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from zentral.security.keys import KeyCustodyError, KeyProvider, WrappedKey

_AAD = b"zentral-data-key"


def generate_kek() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


class LocalFileProvider(KeyProvider):
    name: ClassVar[str] = "local_file"
    production_ready: ClassVar[bool] = False

    def _kek(self) -> bytes:
        if not self.settings.local_kek_file:
            raise KeyCustodyError("the local_file key provider needs ZENTRAL_LOCAL_KEK_FILE")
        try:
            raw = Path(self.settings.local_kek_file).read_text(encoding="ascii").strip()
            kek = base64.b64decode(raw, validate=True)
        except (OSError, ValueError) as exc:
            raise KeyCustodyError(
                f"cannot read ZENTRAL_LOCAL_KEK_FILE ({type(exc).__name__})"
            ) from exc
        if len(kek) != 32:
            raise KeyCustodyError("ZENTRAL_LOCAL_KEK_FILE must hold 32 bytes, base64-encoded")
        return kek

    @staticmethod
    def _id(kek: bytes) -> str:
        return "local:" + hashlib.sha256(kek).hexdigest()[:16]

    def wrap(self, dek: bytes) -> WrappedKey:
        kek = self._kek()
        nonce = secrets.token_bytes(12)
        sealed = AESGCM(kek).encrypt(nonce, dek, _AAD)
        return WrappedKey(self._id(kek), base64.b64encode(nonce + sealed).decode("ascii"))

    def unwrap(self, wrapped: WrappedKey) -> bytes:
        kek = self._kek()
        if wrapped.kek != self._id(kek):
            raise KeyCustodyError("the keyring was wrapped with a different local key file")
        try:
            blob = base64.b64decode(wrapped.value, validate=True)
            return AESGCM(kek).decrypt(blob[:12], blob[12:], _AAD)
        except (InvalidTag, ValueError) as exc:
            raise KeyCustodyError("the wrapped data key could not be unwrapped") from exc

    def describe(self) -> str:
        return f"local_file ({self.settings.local_kek_file})"
