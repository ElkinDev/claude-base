# ADR-006: Portable path policy

Status: Proposed (2026-07-11)

## Context

The content today mixes `%USERPROFILE%`, `$env:USERPROFILE`, and Windows-style joins, and the convention for where proof of work is kept once pointed at a personal absolute path. A public toolkit must never depend on a path that only exists on the author's machine, and its docs must read naturally on every OS.

## Decision

All shipped content uses portable forms, in this order of preference: `~` in prose and docs, `$CLAUDE_PROJECT_DIR` for project-relative hook wiring, render tokens (`{HOME}`, `{GITBASH}`) in templates the installer processes, and profile variables for anything the user may relocate. The evidence root is the profile's `Evidence root:` line, a spec in the token grammar of `docs/EVIDENCE.md` defaulting to `{repo_parent}/evidence`, resolved per machine by `scripts/evidence-path.py` and created on demand, so evidence sits beside the repository and never inside it. Absolute personal paths and drive-letter paths are forbidden outside clearly marked OS-specific examples, and the sanitization guard flags personal home paths in any form.

## Alternatives considered

- Keeping `%USERPROFILE%` as the universal token: meaningless on macOS/Linux docs and leaks a Windows-first tone. Rejected outside Windows-specific snippets.
- Environment variables for everything: powerful but invisible; a profile file is reviewable and committable. Env overrides remain possible where a script documents them.

## Consequences

Docs read the same on every OS. The installer is the only component that knows real absolute paths, and it learns them at install time. Existing files migrate to this policy in Sprint 02 (settings render) and Sprint 03 (docs and skills sweep).
