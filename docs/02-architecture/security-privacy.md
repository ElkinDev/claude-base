# Security and privacy

## Shipped defaults are safe

The engine's `settings.json` ships with the agent's normal permission prompting and a deny list for secret material (SSH keys, `*.pem`, `.env*`, cloud credential files, keychains). It never ships `bypassPermissions` or a dangerous-mode skip. The aggressive personal mode is documented in the README as an explicit, local, at-your-own-risk overlay the user writes into their own `settings.local.json` (ADR-009, FR-042).

## No secrets, ever

The repo contains no credentials, tokens, connection strings, or `.env` values, including in examples; examples use obvious placeholders. The sanitization guard includes generic secret patterns as a second line of defense, but the first line is that nothing secret is ever staged.

## Sanitization model

Two rule sets (ADR-007, FR-040, FR-041):

- Committed generic rules: personal home paths (`C:\Users\<real name>`, `/Users/<name>`, `/home/<name>`), real email addresses, tracker org URLs that are not placeholders, and common credential patterns. Placeholders (`<your-org>`, `%USERPROFILE%`, `~`, `example.com`, `example-app`) are allowlisted.
- Untracked private denylist: the maintainer's local file with the real private project, org, database, and person names. Gitignored, merged by the local guard run, never committed, never echoed into CI logs in full. The meta-rule: the private names must not appear anywhere in the repo, including in the guard config, the specs, commit messages, and CI output.

The guard runs as a local pre-commit/pre-push helper and as a required CI job on every push and PR.

## Supply chain and trust

- No `curl | bash` recommendation. The documented install is clone, read, run (NFR-011). `--dry-run` prints the full plan.
- No compiled artifacts, no embedded blobs; everything is reviewable text.
- No telemetry or network calls from the tooling other than git (NFR-006).
- Third-party content is referenced, not vendored, unless its license is verified compatible and vendoring is explicitly decided (FR-035).
- Optional dependencies (Python, Node) are detected, never auto-installed.

## Hook safety

Hooks are read-mostly: they write only under the worklog root and the project's `.claude/` folder, exit 0 on internal errors, and never run destructive git operations. The upstream fixer touches only local branch config. Any new hook must state its write surface in the hook registry before landing (FR-013).
