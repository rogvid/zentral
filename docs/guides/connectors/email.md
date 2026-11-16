# E-mail (Microsoft 365)

Zentral reads a shared mailbox through Microsoft Graph and sends agents'
replies from the same mailbox.

## 1. A mailbox

Create a shared mailbox, e.g. `servicedesk@example.com`. Make sure your
anti-spoofing policy (SPF, DKIM, DMARC enforcement) applies to it — Zentral
trusts the sender address Exchange reports.

## 2. An app registration for the connector

Use a **dedicated** app registration (not the Zentral API one):

1. *Entra admin center → App registrations → New registration*, single tenant.
2. *API permissions → Add → Microsoft Graph → Application permissions*:
   **Mail.Read** and **Mail.Send**. Grant admin consent.
3. *Certificates & secrets → New client secret*. Note the value, the
   application (client) ID and the tenant ID.

## 3. Restrict the app to the one mailbox

!!! danger "Do not skip this"
    Application permissions are tenant-wide: without this step the connector's
    credentials could read **every mailbox in your organisation**.

Scope the application with Exchange Online *RBAC for Applications*:

```powershell
Connect-ExchangeOnline

New-ServicePrincipal -AppId <APP_CLIENT_ID> -ObjectId <ENTERPRISE_APP_OBJECT_ID> -DisplayName "Zentral mail"

New-ManagementScope -Name "Zentral mailbox" `
  -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'servicedesk@example.com'"

New-ManagementRoleAssignment -App <APP_CLIENT_ID> -Role "Application Mail.Read" -CustomResourceScope "Zentral mailbox"
New-ManagementRoleAssignment -App <APP_CLIENT_ID> -Role "Application Mail.Send" -CustomResourceScope "Zentral mailbox"
```

Then **remove** the tenant-wide Graph permissions from step 2 (the scoped role
assignments replace them), and verify:

```powershell
Test-ServicePrincipalAuthorization -Identity <APP_CLIENT_ID> -Resource servicedesk@example.com   # InScope: True
Test-ServicePrincipalAuthorization -Identity <APP_CLIENT_ID> -Resource someone.else@example.com  # InScope: False
```

(The older `New-ApplicationAccessPolicy` achieves the same and is still
supported.)

## 4. The channel in Zentral

*Administration → Channels → Add channel*, connector **Email graph**:

| Field | Value |
|---|---|
| Configuration | `{"mailbox": "servicedesk@example.com"}` (optional: `"folder": "inbox"`, `"page_size": 25`) |
| Tenant id / Client id / Client secret | From step 2 |
| Default team / system | As you like — a per-system mailbox is a clean way to set the system |

The worker starts polling within `ZENTRAL_POLL_INTERVAL_SECONDS` (default 60).

## How it behaves

- **New mail** → new ticket; the body is stored as plain text.
- **Replies**: agents' replies are sent as `[ZEN-123] <subject>`. When the
  requester answers, the key in the subject threads it onto the ticket —
  *only if the sender is that ticket's requester*. Messages in the same
  Exchange conversation are threaded too.
- **Nothing is changed in the mailbox.** Zentral keeps a cursor (the newest
  received time) instead of marking mail as read; sent replies are saved to
  *Sent Items*.
- **Duplicates** are impossible: each message ID is ingested once.
- **Attachments are ignored** (they stay in the mailbox).

## Operations

- **Client secret expiry**: put the date in your calendar. Replace it under
  *Channels* with a new set of secrets; nothing else changes
  ([key rotation](../../runbooks/key-rotation.md#channel-secrets)).
- **Mail stops arriving**: see
  [Troubleshooting](../../runbooks/troubleshooting.md#no-new-tickets-from-e-mail).
- **Network**: the worker needs outbound HTTPS to `login.microsoftonline.com`
  and `graph.microsoft.com`.

## Other mail systems

Gmail, IMAP and others are not built in. The connector contract is small — see
[Write a connector](../../extending/connectors.md); the Graph connector
(`src/zentral/connectors/email_graph.py`) is the model for a polling connector.
