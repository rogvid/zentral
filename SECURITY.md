# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities **privately**. Do not open a public
issue, pull request or discussion.

Use GitHub's private vulnerability reporting for this repository
(*Security → Report a vulnerability*). Include:

- what you found and where (endpoint, file, version or commit),
- how to reproduce it,
- the impact you believe it has.

Do not include real personal data in a report. Please give the maintainers
reasonable time to fix the issue before disclosing it.

## Scope

In scope: the Zentral server, worker, MCP endpoint, web client, built-in
connectors and the deployment files in this repository.

Out of scope: vulnerabilities in third-party identity providers, model servers
or channels themselves; findings that require a deployment the documentation
explicitly forbids (for example development sign-in exposed to a network).

## Supported versions

Only the latest release receives security fixes.

## For operators

- Security architecture: `docs/architecture/security.md`
- Threat model: `docs/architecture/threat-model.md`
- What is and is not verified: `docs/compliance/security-posture.md`
- If you suspect a breach of your installation: `docs/runbooks/security-incident.md`
