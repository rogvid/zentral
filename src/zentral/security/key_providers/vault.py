"""HashiCorp Vault (or OpenBao) Transit secrets engine.

The KEK is a Transit key; Vault returns ciphertext of the form ``vault:v<N>:…``
that names the key version, so rotating the Transit key needs no change here
(re-wrap with ``zentral keys rewrap`` to move to the newest version, then raise
the key's ``min_decryption_version``).

Grant the application ``update`` on ``<mount>/decrypt/<key>`` only; wrapping
(``<mount>/encrypt/<key>``) is for the administrators who run
``zentral keys`` commands.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, ClassVar

import httpx

from zentral.security.keys import KeyCustodyError, KeyProvider, WrappedKey


class VaultTransitProvider(KeyProvider):
    name: ClassVar[str] = "vault_transit"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        s = self.settings
        if not (s.vault_addr and s.vault_transit_key):
            raise KeyCustodyError(
                "the vault_transit key provider needs ZENTRAL_VAULT_ADDR and "
                "ZENTRAL_VAULT_TRANSIT_KEY"
            )
        if s.is_production and not s.vault_addr.startswith("https://"):
            raise KeyCustodyError("production requires an https ZENTRAL_VAULT_ADDR")
        self._base = s.vault_addr.rstrip("/")
        self._kek = f"{s.vault_transit_mount.strip('/')}/{s.vault_transit_key}"
        self._token: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"X-Vault-Token": self._login()}
        if self.settings.vault_namespace:
            headers["X-Vault-Namespace"] = self.settings.vault_namespace
        return headers

    def _login(self) -> str:
        if self._token:
            return self._token
        s = self.settings
        if s.vault_token_file:
            try:
                self._token = Path(s.vault_token_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise KeyCustodyError(
                    f"cannot read ZENTRAL_VAULT_TOKEN_FILE ({type(exc).__name__})"
                ) from exc
        elif s.vault_kubernetes_role:
            try:
                jwt = Path(s.vault_kubernetes_token_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise KeyCustodyError(
                    f"cannot read the service-account token ({type(exc).__name__})"
                ) from exc
            data = self._post(
                f"/v1/auth/{s.vault_kubernetes_mount.strip('/')}/login",
                {"role": s.vault_kubernetes_role, "jwt": jwt},
                "login",
                authenticated=False,
            )
            self._token = str(data.get("auth", {}).get("client_token", ""))
        else:
            raise KeyCustodyError(
                "the vault_transit key provider needs ZENTRAL_VAULT_TOKEN_FILE or "
                "ZENTRAL_VAULT_KUBERNETES_ROLE"
            )
        if not self._token:
            raise KeyCustodyError("Vault did not issue a token")
        return self._token

    def _post(
        self, path: str, body: dict[str, Any], operation: str, *, authenticated: bool = True
    ) -> dict[str, Any]:
        if authenticated:
            headers = self._headers()
        elif self.settings.vault_namespace:
            headers = {"X-Vault-Namespace": self.settings.vault_namespace}
        else:
            headers = {}
        try:
            response = self.http.post(self._base + path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise KeyCustodyError(f"Vault is unreachable ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise KeyCustodyError(f"Vault refused {operation} (HTTP {response.status_code})")
        try:
            result: dict[str, Any] = response.json()
        except ValueError as exc:
            raise KeyCustodyError("Vault returned an unreadable response") from exc
        return result

    def wrap(self, dek: bytes) -> WrappedKey:
        mount, key = self._kek.split("/", 1)
        plaintext = base64.b64encode(dek).decode()
        data = self._post(f"/v1/{mount}/encrypt/{key}", {"plaintext": plaintext}, "encrypt")
        ciphertext = str(data.get("data", {}).get("ciphertext", ""))
        if not ciphertext.startswith("vault:"):
            raise KeyCustodyError("Vault returned no ciphertext")
        return WrappedKey(self._kek, ciphertext)

    def unwrap(self, wrapped: WrappedKey) -> bytes:
        if wrapped.kek != self._kek:
            raise KeyCustodyError(
                f"the keyring was wrapped by Transit key {wrapped.kek!r}, "
                f"but {self._kek!r} is configured"
            )
        mount, key = self._kek.split("/", 1)
        data = self._post(f"/v1/{mount}/decrypt/{key}", {"ciphertext": wrapped.value}, "decrypt")
        try:
            return base64.b64decode(data["data"]["plaintext"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise KeyCustodyError("Vault returned no plaintext") from exc

    def describe(self) -> str:
        return f"vault_transit ({self._base}, {self._kek})"
