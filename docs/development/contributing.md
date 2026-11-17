# Contributing

## Before you write code

Read the [design principles](../design-principles.md). A change that needs to
bend one of them needs a [decision record](../architecture/decisions.md) first.

## The bar for a change

- [ ] Tests for behaviour **and** for what must not happen
      ([Testing](testing.md))
- [ ] `ruff format`, `ruff check`, `mypy`, `bandit` clean
- [ ] Suite passes on SQLite and PostgreSQL
- [ ] Migration included and hand-reviewed if models changed
- [ ] Docs updated; `scripts/gen_docs.py` run if settings or permissions changed
- [ ] A runbook updated if operators need to do something differently
- [ ] No new dependency without a reason stated in the pull request

## Security review checklist

Ask these of every change. If the answer to any is "yes", say so in the pull
request and show the test.

**Data**

- Does it store or return requester data? → encrypted column; masked without
  `ticket:read_pii`; scrubbed by `scrub_ticket`; included in the export.
- Does it need to *find* something by a personal identifier? → blind index,
  never a clear-text column.
- Could content reach a log line or an audit `details` field? → it must not.

**Access**

- New endpoint or tool? → a permission check in the **service** function, team
  scope via `load_ticket` / `visible_clause`, an entry in the forbidden-matrix
  test.
- New permission or role? → `rbac.py`, the generated matrix, the Entra manifest,
  the segregation-of-duties test.
- Anything unauthenticated? → it needs a very good reason and an entry in the
  allow-list in `test_every_api_route_requires_authentication`.

**Input and output**

- New input model? → `extra="forbid"`, bounded lengths, enumerations rather
  than free strings.
- Text from outside? → `clean_text` / `html_to_text`; rendered as text only.
- New outbound call? → through the shared HTTP client; host on the allow-list;
  no redirects; errors reduced to a status code.

**AI**

- Does model output influence anything new? → validate against an allow-list;
  it may fill gaps, never overwrite a person; no tools for the model.
- Does more text go to the model? → it passes through `redact` first.

**Failure**

- What happens when the dependency is down? → fail closed for security
  decisions, degrade for convenience features.

## Commits and pull requests

- One logical change per pull request; describe *why*.
- Never commit secrets, real e-mail addresses or real ticket content — use
  `example.com` and invented data.
- Vulnerabilities are reported privately (`SECURITY.md`), not as issues or
  pull requests.

## Adding to the plug-in surfaces

Connector, AI provider and authenticator contracts are public API: changing a
signature breaks third-party packages. Extend with optional fields and keep the
example connector (`examples/zentral-connector-slack`) and its test working.
