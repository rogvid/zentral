# Custom authentication

Two built-in models cover most installations and need **no code**:

| Model | Configure with |
|---|---|
| Microsoft Entra ID | [`ZENTRAL_AUTH_MODE=entra`](../operations/identity-entra.md) |
| Any OpenID Connect provider | [`ZENTRAL_AUTH_MODE=oidc`](../operations/identity-oidc.md) — issuer, audience, claim names and a role map |

Reach for code only when tokens are not standard JWTs signed with keys
published at a JWKS endpoint.

## How identity is modelled

A provider is described by an `IdentityProfile` (`zentral/security/tokens.py`):

```python
IdentityProfile(
    name="oidc",
    issuer="https://idp.example.com/realms/main",
    audiences=("zentral-api",),
    algorithms=("RS256",),               # asymmetric algorithms only
    subject_claim="sub",
    scope_claim="scope", required_scope="",
    roles_claim="realm_access.roles",    # dotted path
    groups_claim="groups",
    name_claim="name", email_claim="email",
    role_map={"servicedesk-admins": "Zentral.Admin"},
)
```

`TokenValidator(profile, key_source)` performs every check and produces a
`Principal`. Entra ID and the development provider are just two more profiles
of the same validator — there is one set of checks, not one per provider.

## Plugging in your own authenticator

Anything with this method can authenticate requests for the REST API and MCP:

```python
class Authenticator(Protocol):
    async def validate(self, token: str) -> Principal: ...
```

Create the application with it:

```python
# my_deployment/asgi.py
from zentral.api.app import create_app
from zentral.security.principal import Principal
from zentral.security.tokens import AuthenticationError


class IntrospectingAuthenticator:
    """Example: opaque tokens validated by RFC 7662 introspection."""

    async def validate(self, token: str) -> Principal:
        info = await introspect(token)                    # your call to the IdP
        if not info.get("active") or info.get("aud") != "zentral-api":
            raise AuthenticationError("inactive token")
        return Principal.build(
            subject=info["sub"],
            tenant_id="my-idp",
            kind="user",
            roles=frozenset(map_roles(info)),            # must be Zentral role names
            display_name=info.get("name", ""),
        )


app = create_app(validator=IntrospectingAuthenticator())
```

Run it with `uvicorn my_deployment.asgi:app`.

## Rules for a custom authenticator

- Raise `AuthenticationError` for **every** failure, and fail closed when the
  identity provider cannot be reached.
- `subject` must be stable, unique and never reassigned (not an e-mail
  address). It becomes the actor ID in the audit trail and the key for team
  membership. Maximum 64 characters.
- Return only Zentral role names in `roles`; `Principal.build` derives
  permissions from them. Do not grant permissions directly.
- Check the audience: a token issued for another application must not work
  here.
- `validate` runs on every request — cache what is safe to cache.
- The web client still needs an OIDC authorization-code endpoint to sign people
  in; configure `ZENTRAL_AUTH_MODE=oidc` with your provider's issuer and client
  ID even if the API validates tokens differently.

## Changing roles and permissions

The role → permission mapping lives in `zentral/security/rbac.py` and is the
single source of truth. If you add a permission or role there, update
`scripts/gen_docs.py` (description), run it to regenerate the
[matrix](../compliance/rbac.md), and add the role to
`deploy/entra/app-roles.json`. Tests fail if a role is unmapped.
