# Troubleshooting

Start with:

```bash
zentral doctor
```

and the logs of the API and worker. Every client-visible error carries an
`X-Request-ID`; search the logs for it.

## Sign-in

| Symptom | Check |
|---|---|
| Blank page / "Could not load configuration" | Is the API reachable? `curl https://host/api/v1/config` |
| "Could not reach the identity provider" | Browser cannot fetch the discovery document: IdP down, CORS, or the IdP origin missing from the Content-Security-Policy (`ZENTRAL_CSP_CONNECT_EXTRA`) |
| "Identity provider metadata does not match" | `ZENTRAL_OIDC_ISSUER` ≠ the `issuer` in the discovery document (trailing slash!) |
| Redirect loop | Redirect URI in the IdP must be exactly `https://host/` (type SPA for Entra) |
| "Sign-in response did not match the request" | Sign-in was started in another tab or the session storage was cleared; try again |
| Signed in but "role does not include access" | No Zentral role in the token. `GET /api/v1/me` shows the roles Zentral derived |
| Every API call `401` | See the `authentication_failed` log line; its `check` names the failed check |
| `400` on every request | `Host` header not in `ZENTRAL_ALLOWED_HOSTS` — typical behind a proxy that rewrites the host |

The `check` in `authentication_failed`, a fixed code that holds no token or user data:

| `check` | Meaning |
|---|---|
| `malformed_token` | Not a JWT: the client is sending something else as its bearer token |
| `unsupported_algorithm` | Add the algorithm to `ZENTRAL_OIDC_ALGORITHMS` (asymmetric only) |
| `missing_key_id` | The token's header has no `kid`, so its signing key cannot be found |
| `unknown_signing_key` | Keys not fetched (see `jwks_refresh_failed`) or token from another IdP |
| `token_rejected` | Signature or a standard claim failed: `cause` says which (below) |
| `wrong_tenant` | Token is from another Entra tenant |
| `unusable_subject` | The subject claim is missing, empty or over 64 characters |
| `app_token_has_scopes` | An application token also carries delegated scopes |
| `service_token_no_role` | App role not granted / not mapped |
| `scope_missing` | The web client did not request the API scope |
| `unspecified` | A custom validator refused the token without naming a check |

The `cause` with `token_rejected`:

| `cause` | Meaning |
|---|---|
| `InvalidAudienceError` | Token is for another application: wrong API client ID / audience |
| `InvalidIssuerError` | Wrong tenant/issuer; for Entra, `requestedAccessTokenVersion` is not 2 |
| `ExpiredSignatureError` | Expired - clock skew over 60 s, or a client that does not refresh |

## People cannot see tickets

- Not in the ticket's **team** → add them (*Administration → Teams*). They
  must have signed in once to appear in the list.
- Ticket is **untriaged** and they are a Viewer → Viewers do not see triage.
- Group-linked team but no access → the token has no `groups` claim, or the
  user is in too many groups (Entra omits the claim).
- Sees `[EMAIL]` placeholders → their role lacks `ticket:read_pii` (Viewer,
  Automation). Intended.

## No new tickets from e-mail

1. *Administration → Channels*: the card shows **Failing** with the reason, or
   press **Test** for a live check of sign-in, scope and mailbox access. (The
   same text is in `ingest_sources.last_error` and the worker log.)

2. Read the reason:

    | Error | Cause |
    |---|---|
    | `token endpoint returned 401` | Client secret expired or wrong → new secret, update the channel |
    | `Graph returned 403` | Permissions / mailbox scope not granted (or not yet propagated — can take up to an hour) |
    | `Graph returned 404` | Mailbox address or folder name wrong |
    | `token request failed: ConnectError` | No outbound route to `login.microsoftonline.com` |
    | `the app holds tenant-wide permissions (…)` | The app registration still has Graph `Mail.*` permissions. Scope it with RBAC for Applications and remove them, or configure a scope probe mailbox ([guide](../guides/connectors/email.md#3-grant-access-to-exactly-one-mailbox)) |
    | `credentials can read the probe mailbox` | The access policy does not apply: the app can read other mailboxes. Fix the policy — Zentral will not run until it is denied |
    | `scope probe was inconclusive (Graph returned 404)` | The probe address does not exist; use a real mailbox |
    | `could not read the granted permissions` | Configure a scope probe mailbox |

3. Channel enabled? Mail in the right folder (default *Inbox*)?
4. Mail arrives but no ticket: it was filtered — an automatic reply, a blocked
   sender, a domain outside *Allowed sender domains*, or it was already in the
   mailbox when the channel was created (*First synchronisation: new only*).

## Webhook returns 401

Always the same bare `401`, by design. Check in this order: the URL's source ID
· `Authorization: Bearer zis_…` (not `Token`, no quotes) · the token was
rotated · the channel is disabled. The log line `hook_auth_failed` confirms the
request reached the right channel.

## Tickets are not classified

- Worker not running, or `classify_ticket` jobs failing (`jobs` table).
- `ai_provider_failed` in the worker log → the model is unreachable; tickets
  are classified by keyword rules instead (audit: `fallback: true`).
- Category shows with a "?" → confidence below `ZENTRAL_AI_MIN_CONFIDENCE`;
  it is a suggestion, apply it manually.
- No system suggested → add a description and keywords to the system.

## Replies are not delivered

The message shows *failed* after five attempts.

```sql
SELECT last_error FROM jobs WHERE kind = 'send_message' AND status = 'failed' ORDER BY created_at DESC LIMIT 5;
```

| Error | Cause |
|---|---|
| `Graph returned 403` | `Mail.Send` not granted for the mailbox |
| `requester has no e-mail address` | Ticket came from Teams/Slack/webhook without an address |
| `no reply_webhook_url configured…` | Teams channel without a reply webhook |
| `outbound URL is not allow-listed` | Add the host to `ZENTRAL_OUTBOUND_HOST_ALLOWLIST` |

Replies on tickets created by hand or by webhook show *not applicable*: there
is no channel to send through.

## Start-up fails

| Message | Fix |
|---|---|
| `Unsafe configuration: …` | Read the list; each item names the variable. These checks cannot be bypassed in production |
| `ciphertext could not be decrypted with any configured key` | Wrong `ZENTRAL_ENCRYPTION_KEYS` for this database |
| `Key Vault refused …`, `Vault refused …`, `keyring …` | See [Key custody → Troubleshooting](../operations/key-custody.md#troubleshooting) |
| `/readyz` says schema does not match | Run `zentral db upgrade` (or wait for the migrate job) |
| `error parsing value for field …` | A list or mapping setting is malformed — use `a,b,c` |

## Performance

- Slow inbox with search: search decrypts up to 1,000 tickets in the
  application; filter by status/system first.
- Slow everything: check database latency; the audit chain serialises commits,
  so a slow database disk slows all writes.
- `429`: rate limit per principal (`ZENTRAL_RATE_LIMIT_PER_MINUTE`) — usually
  a script polling too fast.

## Collecting information for a bug report

`zentral doctor` output, the version, the `X-Request-ID`, and the matching log
lines. Logs do not contain ticket content, but they do contain staff IDs and IP
addresses — review before sharing. Report security issues privately
(`SECURITY.md`).
