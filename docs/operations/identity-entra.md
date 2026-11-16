# Identity — Microsoft Entra ID

Zentral uses Entra ID for sign-in and roles. You create **two app
registrations**: one for the API (it defines the roles and the scope) and one
for the web client (a public client that signs people in).

Time: about 20 minutes. You need the *Application Administrator* role (or
equivalent) in the tenant.

## 1. The API application

1. *Entra admin center → Identity → Applications → App registrations → New
   registration*
    - Name: `Zentral API`
    - Supported account types: **this organizational directory only**
    - No redirect URI
2. Note the **Application (client) ID** → `ZENTRAL_ENTRA_API_CLIENT_ID`, and the
   **Directory (tenant) ID** → `ZENTRAL_ENTRA_TENANT_ID`.
3. *Expose an API*:
    - **Application ID URI** → *Add* → accept `api://<client-id>`
    - **Add a scope** → name `access_as_user`, consent *Admins only*, enabled
4. *Manifest* → set `"requestedAccessTokenVersion": 2` (Zentral validates v2.0
   tokens) → *Save*.
5. **App roles** — apply the seven roles from the repository:

    ```bash
    az ad app update --id <API_CLIENT_ID> --app-roles @deploy/entra/app-roles.json
    ```

    or create them by hand under *App roles* with exactly these **values**:
    `Zentral.Admin`, `Zentral.Agent`, `Zentral.Viewer`, `Zentral.Auditor`,
    `Zentral.PrivacyOfficer` (members: users), `Zentral.Automation` (users and
    applications), `Zentral.Ingest` (applications).

## 2. The web client application

1. *New registration*
    - Name: `Zentral Web`
    - This organizational directory only
    - Redirect URI: platform **Single-page application (SPA)**, value
      `https://servicedesk.example.com/` (with the trailing slash)
2. Note the **Application (client) ID** → `ZENTRAL_ENTRA_SPA_CLIENT_ID`.
3. *API permissions → Add a permission → My APIs → Zentral API → Delegated →
   `access_as_user`* → *Grant admin consent*.

No client secret: the web client is a public client using the authorization
code flow with PKCE.

## 3. Assign roles

*Enterprise applications → Zentral API → Users and groups → Add
user/group*: pick people or groups and a role.

- Set *Properties → Assignment required?* to **Yes** on *Zentral API*, so only
  assigned people can obtain a token at all.
- Assign roles to **groups** where your licence allows it; joiners and leavers
  are then handled by group membership.
- Follow the segregation of duties in
  [Roles and permissions](../compliance/rbac.md#segregation-of-duties): admin,
  auditor and privacy officer should be different people.

Role changes take effect when the person's token is renewed (within an hour).

## 4. Configure Zentral

```bash
ZENTRAL_AUTH_MODE=entra
ZENTRAL_ENTRA_TENANT_ID=<tenant-id>
ZENTRAL_ENTRA_API_CLIENT_ID=<api-client-id>
ZENTRAL_ENTRA_SPA_CLIENT_ID=<web-client-id>
```

Run `zentral doctor`, then sign in.

## What Zentral checks on every token

Signature (RS256, keys from your tenant's JWKS) · issuer
`https://login.microsoftonline.com/<tenant>/v2.0` · audience (the API's client
ID or `api://<client-id>`) · `tid` equals your tenant · not expired · for
people: scope `access_as_user` · for applications: at least one app role.
The subject is the **object ID** (`oid`), which is never reassigned.

## Service identities

For scripts, AI agents ([MCP](../guides/mcp.md)) and services that call the
API: register an application per caller, add an **application permission**
(`Zentral.Automation`, `Zentral.Ingest`, …) on *Zentral API*, grant admin
consent, and use the client-credentials flow with scope
`api://<api-client-id>/.default`. Prefer certificates or workload identity
federation over client secrets.

## Optional: group-based teams

To let Entra groups define team membership, add the groups claim to access
tokens: *Zentral API → Token configuration → Add groups claim → Security
groups → Access: Group ID*. Then put the group's object ID on a team in
Zentral.

!!! warning "Many groups"
    Entra omits the `groups` claim when a user is in more than ~200 groups.
    Zentral then sees no groups and falls back to explicit team membership.
    Use *Groups assigned to the application* to keep the claim small.

## Optional: map groups to roles

If you cannot use app roles (for example for licence reasons), map group object
IDs to roles in configuration:

```bash
ZENTRAL_ROLE_MAP=<group-id-1>=Zentral.Admin,<group-id-2>=Zentral.Agent
```

App roles remain the recommended mechanism: they are visible and reviewable in
one place.

## Recommended tenant policies

- Conditional Access on *Zentral API*: require MFA and compliant devices.
- Access reviews on the role assignments ([runbook](../runbooks/access-review.md)).
- Sign-in logs retained per your policy — Entra holds the authentication
  evidence; Zentral's audit trail holds what people did afterwards.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Sign-in loops back to Microsoft | Redirect URI mismatch — it must be type *SPA* and match the public URL exactly, with trailing slash |
| `AADSTS65001` consent error | Admin consent not granted for `access_as_user` |
| Signed in, but every call returns `401` | `requestedAccessTokenVersion` is not 2 (issuer mismatch), or wrong API client ID |
| Signed in, but "role does not include access" | No app role assigned |
| `AADSTS50105` | Assignment required, and this person is not assigned |

`authentication_failed` entries in Zentral's log carry the reason
(`token rejected: InvalidAudienceError`, `required scope missing`, …).
