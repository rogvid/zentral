# Identity — other OIDC providers

Set `ZENTRAL_AUTH_MODE=oidc` to use any OpenID Connect provider: Keycloak,
Okta, Auth0, AD FS, Authentik, Ping, Google Workspace and others. Which
provider an installation uses is purely configuration.

## What the provider must offer

- An OIDC **discovery document** at `<issuer>/.well-known/openid-configuration`
- **JWT access tokens** signed with an asymmetric algorithm (RS256 by default;
  RS/PS/ES 256–512 are supported — HMAC algorithms never are)
- The **authorization code flow with PKCE** for a public client (the web UI)
- A way to put **roles or groups** into the access token
- CORS on the discovery and token endpoints for your Zentral origin

## Settings

| Variable | Meaning | Default |
|---|---|---|
| `ZENTRAL_OIDC_ISSUER` | Issuer URL, exactly as in the token's `iss` | — |
| `ZENTRAL_OIDC_AUDIENCES` | Accepted `aud` values (comma-separated) | — |
| `ZENTRAL_OIDC_CLIENT_ID` | Public client for the web UI | — |
| `ZENTRAL_OIDC_SCOPES` | Scopes the web UI requests | `openid,profile` |
| `ZENTRAL_OIDC_REQUIRED_SCOPE` | Scope a person's token must carry (optional) | *(none)* |
| `ZENTRAL_OIDC_JWKS_URI` | Only if discovery is unavailable | *(discovered)* |
| `ZENTRAL_OIDC_ALGORITHMS` | Accepted signing algorithms | `RS256` |
| `ZENTRAL_OIDC_SUBJECT_CLAIM` | Stable user ID claim | `sub` |
| `ZENTRAL_OIDC_ROLES_CLAIM` | Where roles are; dotted path allowed | `roles` |
| `ZENTRAL_OIDC_GROUPS_CLAIM` | Where groups are | `groups` |
| `ZENTRAL_OIDC_SCOPE_CLAIM` | Scope claim name | `scope` |
| `ZENTRAL_OIDC_NAME_CLAIM`, `ZENTRAL_OIDC_EMAIL_CLAIM` | Display data | `name`, `email` |
| `ZENTRAL_ROLE_MAP` | Map provider roles/groups to Zentral roles | *(none)* |
| `ZENTRAL_CSP_CONNECT_EXTRA` | Extra origins the browser may call, if the token endpoint is not on the issuer's origin | *(none)* |

## Roles

Zentral needs to end up with its own role names (`Zentral.Admin`,
`Zentral.Agent`, …). Two ways:

1. **Name the roles that way in your provider.** Values starting with
   `Zentral.` in the roles claim are used directly.
2. **Map** whatever your provider has:

    ```bash
    ZENTRAL_ROLE_MAP=servicedesk-admins=Zentral.Admin,servicedesk-agents=Zentral.Agent,/staff/dpo=Zentral.PrivacyOfficer
    ```

    Keys are matched against the values of the roles claim **and** the groups
    claim. Anything unmapped grants nothing. A map that targets an unknown role
    stops the process at start-up.

## Provider recipes

=== "Keycloak"

    1. Client `zentral-web`: *Client authentication* off (public), *Standard
       flow* on, redirect URI `https://servicedesk.example.com/`, web origin
       `https://servicedesk.example.com`, PKCE method `S256`.
    2. Client scope `zentral` with an **Audience** mapper adding `zentral-api`
       to access tokens; assign it to `zentral-web` as a default scope.
    3. Realm roles `Zentral.Admin`, `Zentral.Agent`, … (or map your existing
       ones).

    ```bash
    ZENTRAL_AUTH_MODE=oidc
    ZENTRAL_OIDC_ISSUER=https://idp.example.com/realms/main
    ZENTRAL_OIDC_AUDIENCES=zentral-api
    ZENTRAL_OIDC_CLIENT_ID=zentral-web
    ZENTRAL_OIDC_SCOPES=openid,profile,zentral
    ZENTRAL_OIDC_ROLES_CLAIM=realm_access.roles
    ```

=== "Okta"

    Use a **custom authorization server** (the org server issues opaque access
    tokens). Add a `groups` claim to the access token, and an API audience.

    ```bash
    ZENTRAL_AUTH_MODE=oidc
    ZENTRAL_OIDC_ISSUER=https://example.okta.com/oauth2/aus1abcd
    ZENTRAL_OIDC_AUDIENCES=api://zentral
    ZENTRAL_OIDC_CLIENT_ID=0oa1abcd
    ZENTRAL_OIDC_SCOPE_CLAIM=scp
    ZENTRAL_ROLE_MAP=ServiceDesk Admins=Zentral.Admin,ServiceDesk Agents=Zentral.Agent
    ```

    Group names with spaces work in the `key=Role` form; for names containing
    commas or `=`, use JSON:
    `ZENTRAL_ROLE_MAP={"Desk, Admins":"Zentral.Admin"}`.

=== "Auth0"

    Create an **API** (its identifier is the audience) and enable RBAC with
    *Add Permissions in the Access Token*, or add roles with an Action under a
    namespaced claim.

    ```bash
    ZENTRAL_AUTH_MODE=oidc
    ZENTRAL_OIDC_ISSUER=https://example.eu.auth0.com/
    ZENTRAL_OIDC_AUDIENCES=https://servicedesk.example.com/api
    ZENTRAL_OIDC_CLIENT_ID=AbCdEf…
    ZENTRAL_OIDC_ROLES_CLAIM=https://servicedesk.example.com/roles
    ```

    Auth0's issuer ends with a slash — copy it exactly. The web client must
    request the audience; add it under the application's default audience
    setting in the tenant.

=== "AD FS"

    Application group with a *Native application* and a *Web API*; issue group
    membership as the `roles` claim.

    ```bash
    ZENTRAL_AUTH_MODE=oidc
    ZENTRAL_OIDC_ISSUER=https://adfs.example.com/adfs
    ZENTRAL_OIDC_AUDIENCES=https://servicedesk.example.com
    ZENTRAL_OIDC_CLIENT_ID=<native-app-id>
    ZENTRAL_OIDC_SUBJECT_CLAIM=sub
    ```

These recipes follow each provider's documented behaviour but have **not all
been tested against live instances** — verify with the steps below.

## Service accounts

Scripts and AI agents use the client-credentials grant. Their token must have
Zentral's audience and at least one Zentral role (directly or through
`ZENTRAL_ROLE_MAP`). Zentral recognises a token as a *service* when the
provider marks it so (`gty=client-credentials`, the subject equals the client
ID, or a Keycloak `service-account-…` username). This affects labelling only —
permissions always come from roles.

## Verify

```bash
zentral doctor            # "identity provider: oidc: N signing key(s)"
```

Then sign in and open the browser's network tab if anything fails:

| Symptom | Cause |
|---|---|
| "Identity provider metadata does not match" | `ZENTRAL_OIDC_ISSUER` differs from the `issuer` in the discovery document (often a trailing slash) |
| Browser blocks the request to the IdP | CORS not enabled for your origin, or the token endpoint is on another origin → `ZENTRAL_CSP_CONNECT_EXTRA` |
| Signed in, API returns `401` | Inspect the access token: wrong `aud`, opaque token instead of a JWT, or an algorithm not in `ZENTRAL_OIDC_ALGORITHMS`. The log line `authentication_failed` names the reason |
| Signed in, no access | Roles claim path wrong, or values not mapped — `GET /api/v1/me` shows the roles Zentral derived |

## Changing provider later

The subject claim identifies people in team memberships, assignments and the
audit trail. A different provider issues different subjects, so after a switch
people appear as new users: re-add team memberships and reassign open tickets.
The audit trail keeps the old IDs — retain the mapping.
