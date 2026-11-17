"""Azure Key Vault and Azure Key Vault Managed HSM.

The KEK is an RSA key (Key Vault, HSM-backed with the Premium tier) or an RSA /
AES key in a Managed HSM (FIPS 140-3 Level 3). ``wrapKey`` uses the newest key
version; the keyring records the full versioned key ID, so ``unwrapKey`` keeps
working after the key is rotated in Azure until ``zentral keys rewrap`` moves
the keyring to the new version.

Authentication is by managed identity only — there is deliberately no client
secret option, which would put a credential back in the environment:

* App Service / Container Apps (``IDENTITY_ENDPOINT`` + ``IDENTITY_HEADER``)
* AKS workload identity (``AZURE_FEDERATED_TOKEN_FILE``, ``AZURE_CLIENT_ID``,
  ``AZURE_TENANT_ID``)
* Virtual machines (the instance metadata service)

Grant the application's identity only the ``unwrapKey`` permission (a custom
role with ``Microsoft.KeyVault/vaults/keys/unwrap/action``, or the Managed HSM
equivalent); ``wrapKey`` belongs to the administrators who run ``zentral keys``.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from zentral.security.keys import KeyCustodyError, KeyProvider, WrappedKey

API_VERSION = "7.4"
IMDS = "http://169.254.169.254/metadata/identity/oauth2/token"
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
ALGORITHMS = frozenset({"RSA-OAEP-256", "RSA-OAEP", "A256KW"})


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class AzureKeyVaultProvider(KeyProvider):
    name: ClassVar[str] = "azure_keyvault"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        key_id = self.settings.azure_keyvault_key_id.strip().rstrip("/")
        parts = urlsplit(key_id)
        segments = [p for p in parts.path.split("/") if p]
        if (
            parts.scheme != "https"
            or not parts.hostname
            or "." not in parts.hostname
            or len(segments) not in (2, 3)
            or segments[0] != "keys"
        ):
            raise KeyCustodyError(
                "ZENTRAL_AZURE_KEYVAULT_KEY_ID must look like "
                "https://<vault>.vault.azure.net/keys/<name>"
            )
        if self.settings.azure_keyvault_algorithm not in ALGORITHMS:
            raise KeyCustodyError(
                f"ZENTRAL_AZURE_KEYVAULT_ALGORITHM must be one of {', '.join(sorted(ALGORITHMS))}"
            )
        self._host = parts.hostname
        self._key_base = f"https://{self._host}/keys/{segments[1]}"
        self._configured = key_id
        # Token audience: https://vault.azure.net, https://managedhsm.azure.net,
        # and the sovereign clouds' equivalents.
        self._resource = "https://" + self._host.split(".", 1)[1]
        self._token: str | None = None

    # ------------------------------------------------------------- identity
    def _access_token(self) -> str:
        if self._token:
            return self._token
        env = self.environ
        client_id = self.settings.azure_managed_identity_client_id or env.get("AZURE_CLIENT_ID", "")
        try:
            if env.get("IDENTITY_ENDPOINT") and env.get("IDENTITY_HEADER"):
                params = {"resource": self._resource, "api-version": "2019-08-01"}
                if client_id:
                    params["client_id"] = client_id
                response = self.http.get(
                    env["IDENTITY_ENDPOINT"],
                    params=params,
                    headers={"X-IDENTITY-HEADER": env["IDENTITY_HEADER"]},
                )
            elif env.get("AZURE_FEDERATED_TOKEN_FILE"):
                tenant = env.get("AZURE_TENANT_ID", "")
                if not (tenant and client_id):
                    raise KeyCustodyError(
                        "workload identity needs AZURE_TENANT_ID and AZURE_CLIENT_ID"
                    )
                token_file = Path(env["AZURE_FEDERATED_TOKEN_FILE"])
                assertion = token_file.read_text(encoding="utf-8").strip()
                authority = env.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com/")
                response = self.http.post(
                    f"{authority.rstrip('/')}/{tenant}/oauth2/v2.0/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "scope": f"{self._resource}/.default",
                        "client_assertion_type": JWT_BEARER,
                        "client_assertion": assertion,
                    },
                )
            else:
                params = {"resource": self._resource, "api-version": "2018-02-01"}
                if client_id:
                    params["client_id"] = client_id
                response = self.http.get(IMDS, params=params, headers={"Metadata": "true"})
        except (httpx.HTTPError, OSError) as exc:
            raise KeyCustodyError(f"no managed identity token ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise KeyCustodyError(f"no managed identity token (HTTP {response.status_code})")
        try:
            self._token = str(response.json()["access_token"])
        except (KeyError, ValueError) as exc:
            raise KeyCustodyError("the identity endpoint returned no access token") from exc
        return self._token

    def _call(self, url: str, body: dict[str, str], operation: str) -> dict[str, Any]:
        try:
            response = self.http.post(
                url,
                params={"api-version": API_VERSION},
                json=body,
                headers={"Authorization": f"Bearer {self._access_token()}"},
            )
        except httpx.HTTPError as exc:
            raise KeyCustodyError(f"Key Vault is unreachable ({type(exc).__name__})") from exc
        if response.status_code != 200:
            raise KeyCustodyError(f"Key Vault refused {operation} (HTTP {response.status_code})")
        try:
            result: dict[str, Any] = response.json()
        except ValueError as exc:
            raise KeyCustodyError("Key Vault returned an unreadable response") from exc
        return result

    # ------------------------------------------------------------- wrapping
    def wrap(self, dek: bytes) -> WrappedKey:
        alg = self.settings.azure_keyvault_algorithm
        result = self._call(
            f"{self._configured}/wrapkey", {"alg": alg, "value": _b64url(dek)}, "wrapKey"
        )
        kid, value = str(result.get("kid", "")), str(result.get("value", ""))
        version = kid.removeprefix(self._key_base + "/")
        if version == kid or not (version.isalnum() and version.isascii() and value):
            raise KeyCustodyError("Key Vault returned an unexpected wrapKey response")
        return WrappedKey(kid, f"{alg}.{value}")

    def unwrap(self, wrapped: WrappedKey) -> bytes:
        # Only ever send the token to the configured vault and key, whatever the
        # keyring says: a tampered keyring must not redirect the request.
        version = wrapped.kek.removeprefix(self._key_base + "/")
        if version == wrapped.kek or not version.isalnum() or not version.isascii():
            raise KeyCustodyError(
                f"the keyring was wrapped by {wrapped.kek!r}, which is not the configured key"
            )
        alg, _, value = wrapped.value.partition(".")
        if alg not in ALGORITHMS or not value:
            raise KeyCustodyError("the wrapped data key is damaged")
        result = self._call(f"{wrapped.kek}/unwrapkey", {"alg": alg, "value": value}, "unwrapKey")
        try:
            return _unb64url(str(result["value"]))
        except (KeyError, ValueError) as exc:
            raise KeyCustodyError("Key Vault returned no key") from exc

    def describe(self) -> str:
        return f"azure_keyvault ({self._configured})"
