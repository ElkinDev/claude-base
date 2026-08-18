# F10: Sanitization guard

Satisfies: FR-040, FR-041, FR-042 (deny list part). Stories: US-008. Depends on: ADR-007, ADR-009.

## Summary

The tooling that keeps the repo generic forever: one sh checker with committed generic rules plus an untracked private denylist, wired as a local pre-commit/pre-push helper and a required CI job.

## Behavior

- `tools/sanitize-check.sh [--staged|--all|<paths>]`: scans text files, reports findings as `rule-id: file:line`, exits non-zero on any finding. `--staged` covers the pre-commit case; CI uses `--all`.
- Committed generic rules (`tools/sanitize-rules.txt`), each with an id and a regex: personal home paths on any OS (`C:\\Users\\<name>`, `/Users/<name>`, `/home/<name>` where `<name>` is not a sanctioned placeholder), email addresses, tracker org URLs not using `<your-org>` style placeholders, drive-letter absolute paths outside marked OS examples, and common credential shapes (private key headers, connection strings, bearer-like tokens).
- Committed allowlist (`tools/sanitize-allow.txt`): the sanctioned placeholders (`<your-org>`, `<project>`, `example-app`, `example-service`, `example.com`, `%USERPROFILE%` in Windows-specific snippets, `~`), matched before rules.
- Private denylist: `.sanitize/private-denylist.txt`, gitignored (the `.gitignore` entry ships in this feature). One term or regex per line. When present, merged into the rule set with ids `private-<n>`. Findings from private rules print the rule id and location only, never the matched term. CI never has this file.
- Wiring: a documented one-liner installs the pre-commit helper (a two-line hook calling the checker with `--staged`); the CI job (F11) runs `--all` on every push and PR and is required for merge.
- Meta-rule enforced by review, restated in CONTRIBUTING: the private names appear nowhere in the repo, including this feature's own files, commit messages, and branch names.

## Edge cases

- Binary files: skipped by content-type detection.
- False positives in third-party quoted material: per-line waiver comment `sanitize-ok: <rule-id> <reason>`, counted and listed in output so waivers stay visible.
- The denylist file itself must never be committed: the checker fails hard if it finds `.sanitize/private-denylist.txt` tracked by git.

## Out of scope

Git history rewriting (the repo has no commits yet, so history starts clean), hosted secret scanning (complementary, backlog).

## Acceptance

- Seeded-leak tests in CI: fixtures with a fake personal home path, a fake email, and a fake org URL each fail with the right rule id; the allowlisted placeholders pass.
- Local test with a dummy private denylist: a seeded term is caught, and the term itself does not appear in the checker output.
- A tracked denylist file makes the checker fail hard (tested with a temporary index entry in a sandbox repo).
- Full-repo run is green.
