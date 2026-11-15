from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import inspect, text

from tests.helpers import make_source, make_ticket
from zentral.security.crypto import CryptoError, FieldCipher, hash_token, new_ingest_token


def test_roundtrip_and_rotation():
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    before = FieldCipher([old], "k" * 32)
    token = before.encrypt("hemmelig")
    assert "hemmelig" not in token

    after = FieldCipher([new, old], "k" * 32)  # new primary, old still readable
    assert after.decrypt(token) == "hemmelig"
    rotated = after.rotate(token)
    assert FieldCipher([new], "k" * 32).decrypt(rotated) == "hemmelig"
    with pytest.raises(CryptoError):
        FieldCipher([new], "k" * 32).decrypt(token)


def test_tampered_ciphertext_is_rejected():
    cipher = FieldCipher([Fernet.generate_key().decode()], "k" * 32)
    token = cipher.encrypt("data")
    with pytest.raises(CryptoError):
        cipher.decrypt(token[:-4] + "AAAA")


def test_blind_index_is_keyed_normalised_and_namespaced():
    key = Fernet.generate_key().decode()
    a, b = FieldCipher([key], "k" * 32), FieldCipher([key], "x" * 32)
    assert a.blind_index("email", " Jane@Example.com ") == a.blind_index(
        "email", "jane@example.com"
    )
    assert a.blind_index("email", "x") != a.blind_index("teams", "x")
    assert a.blind_index("email", "x") != b.blind_index("email", "x")


def test_misconfiguration_fails():
    with pytest.raises(CryptoError):
        FieldCipher([], "k" * 32)
    with pytest.raises(CryptoError):
        FieldCipher(["not-a-key"], "k" * 32)


def test_ingest_tokens_are_random_and_only_hash_is_derivable():
    (t1, h1), (t2, _) = new_ingest_token(), new_ingest_token()
    assert t1 != t2 and t1.startswith("zis_") and len(t1) > 40
    assert h1 == hash_token(t1) and t1 not in h1


async def test_no_plaintext_personal_data_in_database(client, auth, runtime, worker):
    """Dump every text column of every table and look for the secrets."""
    await make_ticket(
        client,
        auth,
        subject="Payroll for Jane Doe",
        body="My IBAN is DK5000400440116243",
        requester_email="jane.doe@example.com",
        requester_name="Jane Doe",
    )
    await make_source(client, auth, "teams", secrets={"hmac_secret": "c2VjcmV0LXNlY3JldA=="})
    hook = await make_source(client, auth, "webhook", name="hooks")
    await worker.run_once()

    dump = []
    async with runtime.engine.connect() as conn:
        tables = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
        for table in tables:
            rows = (await conn.execute(text(f'SELECT * FROM "{table}"'))).all()
            dump.append(str(rows))
    haystack = "\n".join(dump)
    for secret in (
        "Jane",
        "jane.doe",
        "Payroll",
        "DK5000400440116243",
        "c2VjcmV0LXNlY3JldA==",
        hook["ingest_token"],
    ):
        assert secret not in haystack, f"{secret!r} is stored in clear"
    assert "ticket.created" in haystack  # sanity: the dump does contain data
