# Dependencies and advisories

## How dependencies are kept current

- **Pinned exactly** by the lock files: `uv.lock` (Python) and
  `web/package-lock.json` (web client). The image builds with `--frozen` /
  `npm ci`, so it only ever contains what was reviewed.
- **Dependabot** proposes updates weekly for Python, npm, GitHub Actions and
  the Docker base images (`.github/dependabot.yml`), against `main`. Major
  upgrades get a full CI run, including PostgreSQL and the browser end-to-end
  walkthrough, before they are merged.
- **CI audits** every change: `pip-audit` for Python and
  `npm audit --omit=dev --audit-level=high` for the web client's runtime
  dependencies. A high or critical advisory in anything that ships fails the
  build.

Development-only packages (bundler, test runner, type checker) are not in the
image or in the built web client, so CI does not fail on their advisories.
Triage them here instead.

## Triaging an advisory

1. **Does it ship?** `npm ls <package> --omit=dev` and a search of `web/dist/`
   for the web client; `uv tree --no-dev` for Python. If it ships, it is a
   release blocker: upgrade, or patch with an `overrides` entry, or replace the
   package.
2. **If dev-only: can the vulnerable path be reached?** Check what input the
   advisory needs and whether anything untrusted reaches it during a build or
   a test run.
3. **Record the decision** in the table below, and dismiss the Dependabot alert
   with the matching reason ("Vulnerable code is not actually used" or "Risk is
   tolerable") and a link to this page.
4. **Re-check** when a patched version appears: Dependabot opens the update.

## Open advisories

| Advisory | Package (via) | Ships? | Decision | Re-check |
|---|---|---|---|---|
| [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) (CVE-2026-112366), high, ReDoS | `picomatch` ≤ 4.0.7, via `vite`, `vitest`, `tinyglobby`, `fdir` (web build tooling) | **No**: not in the runtime dependency tree, not in `web/dist/`, not in the image | **Accepted, not exploitable.** The attack needs an attacker-supplied glob pattern; picomatch only sees the fixed patterns in our own Vite/Vitest configuration, at build and test time. No patched version exists (advisory updated 2026-11-17). npm reports 6 findings: this one advisory, counted once per dependent package. | When picomatch > 4.0.7 is released: Dependabot's weekly web group update |
