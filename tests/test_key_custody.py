"""Key custody: envelope-encrypted keyring, key providers, CLI and production rules."""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from typer.testing import CliRunner

from tests.conftest import make_settings
from zentral.cli import app
from zentral.config import ConfigurationError, get_settings
from zentral.security import keys
from zentral.security.key_providers.azure import AzureKeyVaultProvider
from zentral.security.key_providers.local import generate_kek
from zentral.security.key_providers.vault import VaultTransitProvider
from zentral.security.keys import KeyCustodyError, KeyMaterial, WrappedKey

NO_ENV_KEYS = {
    "encryption_keys": [],
    "blind_index_key": "",
    "audit_hmac_key": "",
}


@pytest.fixture
def kek_file(tmp_path) -> Path:
    path = tmp_path / "kek"
    path.write_text(generate_kek())
    return path


def local_settings(kek_file: Path, **overrides):
    base = {**NO_ENV_KEYS, "key_provider": "local_file", "local_kek_file": str(kek_file)}
    return make_settings(**{**base, **overrides})


# --- keyring ---------------------------------------------------------------------


def test_seal_and_open_round_trip(kek_file):
    material = KeyMaterial.generate().with_new_audit_key()
    with httpx.Client() as http:
        provider = keys.load_provider(local_settings(kek_file), http)
        document = keys.seal(material, provider)
        assert keys.open_keyring(document, provider) == material
    text = json.dumps(document)
    for secret in (*material.encryption_keys, material.blind_index_key, material.audit_hmac_key):
        assert secret not in text
    assert document["provider"] == "local_file" and document["kek"].startswith("local:")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created", "2020-01-01T00:00:00+00:00"),
        ("ciphertext", base64.b64encode(b"x" * 64).decode()),
        ("nonce", base64.b64encode(b"n" * 12).decode()),
    ],
)
def test_tampered_keyring_is_refused(kek_file, field, value):
    with httpx.Client() as http:
        provider = keys.load_provider(local_settings(kek_file), http)
        document = keys.seal(KeyMaterial.generate(), provider)
        document[field] = value
        with pytest.raises(KeyCustodyError, match="integrity"):
            keys.open_keyring(document, provider)


def test_keyring_from_another_provider_or_key_is_refused(kek_file, tmp_path):
    other = tmp_path / "other-kek"
    other.write_text(generate_kek())
    with httpx.Client() as http:
        document = keys.seal(
            KeyMaterial.generate(), keys.load_provider(local_settings(kek_file), http)
        )
        with pytest.raises(KeyCustodyError, match="different local key"):
            keys.open_keyring(document, keys.load_provider(local_settings(other), http))
        with pytest.raises(KeyCustodyError, match="wrapped by 'local_file'"):
            keys.open_keyring(
                {**document, "provider": "local_file"},
                VaultTransitProvider(
                    make_settings(vault_addr="http://vault:8200", vault_transit_key="k"), http
                ),
            )


def test_invalid_material_is_never_sealed(kek_file):
    with httpx.Client() as http, pytest.raises(KeyCustodyError, match="refusing"):
        keys.seal(
            KeyMaterial(("not-a-fernet-key",), "b" * 40, "a" * 40),
            keys.load_provider(local_settings(kek_file), http),
        )


def test_key_material_never_prints_itself():
    material = KeyMaterial.generate()
    assert material.blind_index_key not in repr(material)
    assert material.encryption_keys[0] not in f"{material}"


# --- materialise and the production rules ------------------------------------------


def write_local_keyring(kek_file: Path, path: Path, material: KeyMaterial | None = None):
    material = material or KeyMaterial.generate()
    with httpx.Client() as http:
        doc = keys.seal(material, keys.load_provider(local_settings(kek_file), http))
    keys.write_keyring(path, doc)
    return material


def test_materialise_fills_the_settings_from_the_keyring(kek_file, tmp_path):
    material = write_local_keyring(kek_file, tmp_path / "keyring.json")
    settings = local_settings(kek_file, keyring_file=str(tmp_path / "keyring.json"))
    with pytest.raises(ConfigurationError, match="not been unwrapped"):
        settings.validate_for_runtime()
    resolved = keys.materialise(settings)
    resolved.validate_for_runtime()
    assert KeyMaterial.from_settings(resolved) == material
    assert keys.materialise(resolved) is resolved  # idempotent
    assert (tmp_path / "keyring.json").stat().st_mode & 0o777 == 0o600


def test_inline_keyring(kek_file, tmp_path):
    write_local_keyring(kek_file, tmp_path / "k.json")
    settings = local_settings(kek_file, keyring=(tmp_path / "k.json").read_text())
    assert keys.materialise(settings).encryption_keys
    with pytest.raises(KeyCustodyError, match="not both"):
        keys.materialise(
            local_settings(
                kek_file, keyring=settings.keyring, keyring_file=str(tmp_path / "k.json")
            )
        )


def test_env_keys_next_to_a_provider_are_refused(kek_file, tmp_path):
    write_local_keyring(kek_file, tmp_path / "k.json")
    settings = local_settings(
        kek_file, keyring_file=str(tmp_path / "k.json"), audit_hmac_key="a" * 40
    )
    with pytest.raises(KeyCustodyError, match="remove ZENTRAL_AUDIT_HMAC_KEY"):
        keys.materialise(settings)


def test_missing_keyring_is_explained(kek_file):
    with pytest.raises(KeyCustodyError, match="needs ZENTRAL_KEYRING_FILE"):
        keys.materialise(local_settings(kek_file))


def test_production_refuses_plain_env_keys_unless_allowed():
    base = {
        "env": "production",
        "auth_mode": "entra",
        "entra_tenant_id": "t",
        "entra_api_client_id": "c",
        "database_url": "postgresql+asyncpg://u@db/zentral",
        "public_base_url": "https://desk.corp.example",
    }
    with pytest.raises(ConfigurationError, match="plain-text keys"):
        make_settings(**base).validate_for_runtime()
    allowed = make_settings(**base, allow_env_keys=True)
    allowed.validate_for_runtime()
    assert keys.custody_summary(allowed)[0] == "warn"


def test_production_refuses_the_local_file_provider(kek_file, tmp_path):
    write_local_keyring(kek_file, tmp_path / "k.json")
    settings = local_settings(kek_file, env="production", keyring_file=str(tmp_path / "k.json"))
    with pytest.raises(KeyCustodyError, match="not allowed in production"):
        keys.materialise(settings)
    # …but an offline escrow copy may still be *written* with it.
    with httpx.Client() as http:
        escrow = keys.load_provider(settings, http, for_escrow=True)
        assert keys.seal(KeyMaterial.generate(), escrow)["provider"] == "local_file"


def test_unknown_provider():
    with pytest.raises(KeyCustodyError, match="unknown key provider"):
        keys.provider_class("nope")


# --- Azure Key Vault -----------------------------------------------------------------

VAULT = "https://desk-kv.vault.azure.net"
KEY = f"{VAULT}/keys/zentral-kek"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class FakeKeyVault:
    """RSA-OAEP-256 wrap/unwrap with key versions, behind managed-identity auth."""

    OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)

    def __init__(self) -> None:
        self.versions: dict[str, rsa.RSAPrivateKey] = {}
        self.latest = ""
        self.requests: list[httpx.Request] = []
        self.unwrap_allowed = True
        self.rotate()

    def rotate(self) -> None:
        self.latest = secrets.token_hex(16)
        self.versions[self.latest] = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "169.254.169.254":
            assert request.headers["Metadata"] == "true"
            assert url.params["resource"] == "https://vault.azure.net"
            return httpx.Response(200, json={"access_token": "mi-token"})
        if url.host != "desk-kv.vault.azure.net":
            return httpx.Response(599)
        if request.headers.get("Authorization") != "Bearer mi-token":
            return httpx.Response(401)
        parts = url.path.strip("/").split("/")  # keys, name, [version], op
        op = parts[-1]
        version = parts[2] if len(parts) == 4 else self.latest
        if version not in self.versions:
            return httpx.Response(404)
        body = json.loads(request.content)
        assert body["alg"] == "RSA-OAEP-256"
        key = self.versions[version]
        if op == "wrapkey":
            value = key.public_key().encrypt(_unb64url(body["value"]), self.OAEP)
        elif op == "unwrapkey" and self.unwrap_allowed:
            value = key.decrypt(_unb64url(body["value"]), self.OAEP)
        else:
            return httpx.Response(403)
        return httpx.Response(200, json={"kid": f"{KEY}/{version}", "value": _b64url(value)})


@pytest.fixture
def azure():
    fake = FakeKeyVault()
    with httpx.Client(transport=httpx.MockTransport(fake)) as http:
        yield fake, http


def azure_provider(http, **overrides):
    base = {**NO_ENV_KEYS, "key_provider": "azure_keyvault", "azure_keyvault_key_id": KEY}
    settings = make_settings(**{**base, **overrides})
    return AzureKeyVaultProvider(settings, http, environ={})


def test_azure_wraps_and_unwraps_with_a_managed_identity(azure):
    fake, http = azure
    provider = azure_provider(http)
    material = KeyMaterial.generate()
    document = keys.seal(material, provider)
    assert document["kek"] == f"{KEY}/{fake.latest}"
    assert keys.open_keyring(document, azure_provider(http)) == material
    # The data key never travels in clear except to and from the vault.
    assert all(material.audit_hmac_key.encode() not in (r.content or b"") for r in fake.requests)


def test_azure_keeps_unwrapping_after_the_kek_is_rotated_and_rewraps(azure):
    fake, http = azure
    material = KeyMaterial.generate()
    old = keys.seal(material, azure_provider(http))
    fake.rotate()
    assert keys.open_keyring(old, azure_provider(http)) == material
    new = keys.seal(keys.open_keyring(old, azure_provider(http)), azure_provider(http))
    assert new["kek"].endswith(fake.latest) and new["kek"] != old["kek"]


@pytest.mark.parametrize(
    "kek",
    [
        "https://attacker.example/keys/zentral-kek/abc",
        f"{VAULT}/keys/other-key/abc",
        f"{KEY}/../../secrets/x",
        f"{KEY}/abc?x=1",
    ],
)
def test_azure_never_follows_a_keyring_elsewhere(azure, kek):
    fake, http = azure
    with pytest.raises(KeyCustodyError, match="not the configured key"):
        azure_provider(http).unwrap(WrappedKey(kek, "RSA-OAEP-256.AAAA"))
    assert fake.requests == []


def test_azure_errors_name_the_operation_not_the_secret(azure):
    fake, http = azure
    document = keys.seal(KeyMaterial.generate(), azure_provider(http))
    fake.unwrap_allowed = False
    with pytest.raises(KeyCustodyError, match=r"refused unwrapKey \(HTTP 403\)"):
        keys.open_keyring(document, azure_provider(http))


@pytest.mark.parametrize(
    "key_id",
    ["http://desk-kv.vault.azure.net/keys/k", "https://desk-kv.vault.azure.net/secrets/k", ""],
)
def test_azure_key_id_is_validated(key_id):
    with httpx.Client() as http, pytest.raises(KeyCustodyError, match="must look like"):
        azure_provider(http, azure_keyvault_key_id=key_id)


def test_azure_app_service_and_workload_identity(tmp_path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "t"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        settings = make_settings(
            azure_keyvault_key_id="https://hsm1.managedhsm.azure.net/keys/k",
            azure_managed_identity_client_id="uami",
        )
        AzureKeyVaultProvider(
            settings,
            http,
            environ={"IDENTITY_ENDPOINT": "http://localhost:42/msi", "IDENTITY_HEADER": "h"},
        )._access_token()
        assert seen[-1].headers["X-IDENTITY-HEADER"] == "h"
        assert seen[-1].url.params["resource"] == "https://managedhsm.azure.net"
        assert seen[-1].url.params["client_id"] == "uami"

        token = tmp_path / "sa-token"
        token.write_text("federated-jwt")
        AzureKeyVaultProvider(
            settings,
            http,
            environ={"AZURE_FEDERATED_TOKEN_FILE": str(token), "AZURE_TENANT_ID": "tid"},
        )._access_token()
        form = dict(x.split("=", 1) for x in seen[-1].content.decode().split("&"))
        assert str(seen[-1].url) == "https://login.microsoftonline.com/tid/oauth2/v2.0/token"
        assert form["client_assertion"] == "federated-jwt" and form["client_id"] == "uami"
        assert "managedhsm.azure.net%2F.default" in form["scope"]


# --- HashiCorp Vault Transit ------------------------------------------------------------


class FakeVault:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.secret = secrets.token_bytes(32)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        path = request.url.path
        if path == "/v1/auth/kubernetes/login":
            assert body == {"role": "zentral", "jwt": "sa-jwt"}
            return httpx.Response(200, json={"auth": {"client_token": "k8s-token"}})
        if request.headers.get("X-Vault-Token") not in ("file-token", "k8s-token"):
            return httpx.Response(403)
        if path == "/v1/transit/encrypt/zentral":
            raw = base64.b64decode(body["plaintext"])
            sealed = bytes(a ^ b for a, b in zip(raw, self.secret, strict=True))
            return httpx.Response(
                200, json={"data": {"ciphertext": "vault:v1:" + base64.b64encode(sealed).decode()}}
            )
        if path == "/v1/transit/decrypt/zentral":
            sealed = base64.b64decode(body["ciphertext"].removeprefix("vault:v1:"))
            raw = bytes(a ^ b for a, b in zip(sealed, self.secret, strict=True))
            return httpx.Response(200, json={"data": {"plaintext": base64.b64encode(raw).decode()}})
        return httpx.Response(404)


def vault_settings(**overrides):
    return make_settings(
        **{
            **NO_ENV_KEYS,
            "key_provider": "vault_transit",
            "vault_addr": "https://vault.corp.example:8200",
            "vault_transit_key": "zentral",
            **overrides,
        }
    )


def test_vault_transit_with_a_token_file(tmp_path):
    token = tmp_path / "token"
    token.write_text("file-token\n")
    fake = FakeVault()
    with httpx.Client(transport=httpx.MockTransport(fake)) as http:
        settings = vault_settings(vault_token_file=str(token), vault_namespace="ops")
        material = KeyMaterial.generate()
        document = keys.seal(material, keys.load_provider(settings, http))
        assert document["kek"] == "transit/zentral"
        assert document["wrapped_key"].startswith("vault:v1:")
        assert keys.open_keyring(document, keys.load_provider(settings, http)) == material
    assert all(r.headers["X-Vault-Namespace"] == "ops" for r in fake.requests)


def test_vault_transit_with_kubernetes_auth(tmp_path):
    jwt = tmp_path / "sa"
    jwt.write_text("sa-jwt")
    fake = FakeVault()
    with httpx.Client(transport=httpx.MockTransport(fake)) as http:
        settings = vault_settings(
            vault_kubernetes_role="zentral", vault_kubernetes_token_file=str(jwt)
        )
        document = keys.seal(KeyMaterial.generate(), keys.load_provider(settings, http))
        keys.open_keyring(document, keys.load_provider(settings, http))
        with pytest.raises(KeyCustodyError, match="Transit key 'transit/other'"):
            keys.load_provider(settings, http).unwrap(WrappedKey("transit/other", "vault:v1:x"))


def test_vault_transit_configuration_is_checked():
    with httpx.Client() as http:
        with pytest.raises(KeyCustodyError, match="needs ZENTRAL_VAULT_ADDR"):
            keys.load_provider(vault_settings(vault_addr=""), http)
        with pytest.raises(KeyCustodyError, match="https"):
            keys.load_provider(vault_settings(env="production", vault_addr="http://v:8200"), http)
        with pytest.raises(KeyCustodyError, match="TOKEN_FILE or"):
            keys.load_provider(vault_settings(), http).wrap(b"x" * 32)


# --- the CLI, end to end with the local_file provider -------------------------------------


@pytest.fixture
def keyring_env(tmp_path, monkeypatch, kek_file):
    """An installation moving from environment keys to a keyring."""
    for name in list(__import__("os").environ):
        if name.startswith("ZENTRAL_"):
            monkeypatch.delenv(name)
    material = KeyMaterial.generate()
    env = {
        "ZENTRAL_ENV": "test",
        "ZENTRAL_AUTH_MODE": "dev",
        "ZENTRAL_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'k.db'}",
        "ZENTRAL_LOG_LEVEL": "ERROR",
        "ZENTRAL_KEY_PROVIDER": "local_file",
        "ZENTRAL_LOCAL_KEK_FILE": str(kek_file),
        "ZENTRAL_ENCRYPTION_KEYS": material.encryption_keys[0],
        "ZENTRAL_BLIND_INDEX_KEY": material.blind_index_key,
        "ZENTRAL_AUDIT_HMAC_KEY": material.audit_hmac_key,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    def drop_env_keys(keyring: Path) -> None:
        for name in (
            "ZENTRAL_ENCRYPTION_KEYS",
            "ZENTRAL_BLIND_INDEX_KEY",
            "ZENTRAL_AUDIT_HMAC_KEY",
        ):
            monkeypatch.delenv(name)
        monkeypatch.setenv("ZENTRAL_KEYRING_FILE", str(keyring))
        get_settings.cache_clear()

    yield material, drop_env_keys
    get_settings.cache_clear()


def _run(*args: str):
    result = CliRunner().invoke(app, list(args))
    return result


def test_migrate_to_a_keyring_and_rotate_everything(keyring_env, tmp_path, kek_file):
    from zentral.db.models import Ticket
    from zentral.runtime import build_runtime

    material, drop_env_keys = keyring_env
    keyring = tmp_path / "keyring.json"
    # 1. Plain-text keys in use: create data.
    with pytest.raises(KeyCustodyError, match="remove ZENTRAL_ENCRYPTION_KEYS"):
        build_runtime(get_settings())
    result = _run("keys", "wrap", "--output", str(keyring))
    assert result.exit_code == 0, result.output
    assert material.audit_hmac_key not in keyring.read_text()

    drop_env_keys(keyring)
    assert _run("db", "upgrade").exit_code == 0

    async def seed() -> None:
        rt = build_runtime(get_settings())
        async with rt.sessions() as session:
            ctx = rt.system_ctx(session, "test", "cli")
            ticket = Ticket(subject="Printer on fire", channel="manual", tags=[])
            session.add(ticket)
            await session.flush()
            ctx.audit("ticket.created", "ticket", ticket.id)
            await ctx.commit()
        await rt.aclose()

    async def subject() -> str:
        rt = build_runtime(get_settings())
        async with rt.sessions() as session:
            value = (await session.get(Ticket, 1)).subject
        await rt.aclose()
        return value

    asyncio.run(seed())
    assert KeyMaterial.from_settings(keys.materialise(get_settings())) == material

    # 2. Doctor names the custody; `keys show` describes without revealing.
    doctor = _run("doctor")
    assert "✓ key custody: local_file: data key wrapped by local:" in doctor.output
    shown = _run("keys", "show").output
    assert keys.fingerprint(material.encryption_keys[0]) in shown
    assert material.encryption_keys[0] not in shown and material.blind_index_key not in shown

    # 3. Field-encryption key rotation: refused to retire before re-encrypting.
    assert _run("keys", "add-data-key").exit_code == 0
    assert len(list(tmp_path.glob("keyring.json.*.bak"))) == 1
    refused = _run("keys", "retire-data-keys")
    assert refused.exit_code == 1 and "run `zentral keys rotate` first" in refused.output
    assert _run("keys", "rotate").exit_code == 0
    assert _run("keys", "retire-data-keys").exit_code == 0
    now = KeyMaterial.from_settings(keys.materialise(get_settings()))
    assert len(now.encryption_keys) == 1 and now.encryption_keys != material.encryption_keys
    assert asyncio.run(subject()) == "Printer on fire"

    # 4. Audit key rotation keeps the trail verifiable.
    assert _run("keys", "rotate-audit-key").exit_code == 0
    verify = _run("audit", "verify")
    assert verify.exit_code == 0, verify.output
    assert now.blind_index_key == material.blind_index_key

    # 5. Re-wrapping (after a KEK rotation) changes the envelope, not the keys.
    copy = tmp_path / "rewrapped.json"
    assert _run("keys", "rewrap", "--output", str(copy)).exit_code == 0
    before = json.loads(keyring.read_text())
    after = json.loads(copy.read_text())
    assert after["wrapped_key"] != before["wrapped_key"]
    with httpx.Client() as http:
        provider = keys.load_provider(get_settings(), http)
        assert keys.open_keyring(after, provider) == keys.open_keyring(before, provider)
    # 6. The escrow copy is verified against the keys in use, and goes stale on change.
    escrow_kek = tmp_path / "escrow-kek"
    assert _run("keys", "new-local-kek", str(escrow_kek)).exit_code == 0
    with httpx.Client() as http:
        escrow_doc = keys.seal(
            KeyMaterial.from_settings(keys.materialise(get_settings())),
            keys.load_provider(local_settings(escrow_kek), http),
        )
    escrow = tmp_path / "escrow.json"
    keys.write_keyring(escrow, escrow_doc)
    args = ("keys", "verify-escrow", "--keyring", str(escrow), "--kek-file", str(escrow_kek))
    ok = _run(*args)
    assert ok.exit_code == 0 and "matches the keys in use" in ok.output
    assert _run("keys", "rotate-audit-key").exit_code == 0
    stale = _run(*args)
    assert stale.exit_code == 1 and "holds different keys" in stale.output
    wrong = _run("keys", "verify-escrow", "--keyring", str(escrow), "--kek-file", str(kek_file))
    assert wrong.exit_code == 1


def test_init_refuses_to_overwrite_a_keyring(keyring_env, tmp_path):
    keyring = tmp_path / "new.json"
    assert _run("keys", "init", "--output", str(keyring)).exit_code == 0
    again = _run("keys", "init", "--output", str(keyring))
    assert again.exit_code == 1 and "already exists" in again.output


def test_production_init_writes_a_key_provider_not_keys(tmp_path, monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("ZENTRAL_"):
            monkeypatch.delenv(name)
    out = tmp_path / ".env"
    assert _run("init", "--auth", "entra", "--output", str(out)).exit_code == 0
    text = out.read_text()
    assert "ZENTRAL_KEY_PROVIDER=azure_keyvault" in text
    assert "ZENTRAL_ENCRYPTION_KEYS" not in text and "ZENTRAL_AUDIT_HMAC_KEY" not in text
