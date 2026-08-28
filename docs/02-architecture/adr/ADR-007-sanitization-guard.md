# ADR-007: Sanitization guard with an untracked private denylist

Status: Proposed (2026-07-11). Amended 2026-08-28.

## Context

This repo unifies content that originated in private, business-specific setups. The names of those projects, orgs, databases, and people must never reach the public repo. The obvious enforcement, a committed denylist, would itself publish the private names.

## Decision

Two-part guard, one tool (`scripts/sanitize-check.py`), run locally through a pre-push hook (pre-commit optional) and in CI as a required job (F10):

- Committed generic rules: patterns that identify leaks without naming them, for example real personal home paths on any OS, real email addresses, tracker org URLs that are not `<your-org>` placeholders, and common credential shapes. A committed allowlist covers the sanctioned placeholders.
- Untracked private denylist: a gitignored local file holding the real private names. The local guard merges it when present; CI never sees it. Findings from the private list are reported by rule id and location, never by echoing the matched secret name into logs that could be shared.

Meta-rule: the private names appear nowhere in the repo, including guard config, specs, commit messages, branch names, and CI output. This ADR is itself written under that rule.

## Amendment (2026-08-28)

Amended 2026-08-28: the checker is Python, the hook wrapper is sh, and the files live under `scripts/`. The original decision said one POSIX sh tool at `tools/sanitize-check.sh`. Two things changed the shape without changing the decision. First, `scripts/doctor.py` already makes Python 3.8 or newer a hard requirement of the kit, so a Python checker adds no dependency, while the regex dialects, the binary-file detection and the denylist merge behave differently under BSD and GNU grep, which is the portability problem this guard exists to avoid. Second, git runs hooks under sh on every OS, Git for Windows included, so the thin wrapper that git executes stays sh and probes for a usable interpreter before calling the checker. The location follows the repo as it is: everything else in the kit's tooling lives in `scripts/`, and there is no `tools/` directory. The decision's CI half stands but is not delivered yet: the required job belongs to F11, so until that lands the guard runs locally only.

## Alternatives considered

- Committed denylist: self-defeating, publishes the names. Rejected.
- Manual review only: failed already once during spec writing; humans and models both slip. Rejected as sole mechanism.
- Hosted secret-scanning services: complementary, but they target credentials, not business names. May be added, does not replace the guard.

## Consequences

Porting private assets becomes safe-by-process: the local run catches name leaks before commit, CI catches generic leaks from any contributor. The maintainer must maintain the local denylist and keep a private backup of it, since it is deliberately not in the repo.
