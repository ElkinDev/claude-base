# F09: Cross-agent project hooks

Satisfies: FR-037. Stories: US-010. Depends on: ADR-002, ADR-005 (Node exception), ADR-007.

## Summary

Ship the two proven cross-agent Node hooks as optional project templates: an edit-time validator that lints, format-checks, and compiles changed files regardless of which agent edited them, and a pre-push gate that blocks pushes when the project's check command fails for commits touching configured paths.

## Behavior

- Location and install: `project-template/.agents/hooks/validate-changes.js` and `pre-push-check.js`, installed with the project scaffold when Node is present (skipped with a note otherwise); wiring examples for Claude Code (PreToolUse and PostToolUse in `.claude/settings.local.json`), Copilot, and a manual mode for agents without hook systems, documented next to the files.
- Configuration file `.agents/hooks/config.json`: watched path globs, the per-extension validate commands, the gate command (defaults to the profile's quality-gates command), and the protected paths for the push gate. The private originals' hardcoded frontend path becomes a glob in this config (FR-037).
- Payload normalization: the validator accepts Claude hook JSON, Copilot payloads, and a plain file-list argv form, normalizing to one internal shape, as in the private originals.
- Failure semantics: the validator reports findings and exits non-zero so the agent surfaces them; the push gate prints the failing command output and blocks. Both exit 0 on their own internal errors to avoid breaking sessions, logging the error to stderr.

## Edge cases

- Repos without the configured tooling (no linter): config entries are optional; absent entries mean skip, stated in the output.
- Very large changed-file sets: the validator processes at most a documented number of files per run and reports the overflow.
- Windows: commands run through Node's shell handling, covered by a CI smoke on the matrix.

## Out of scope

Shipping any linter or compiler, git hook installation automation (the doc shows the one-liner to wire `pre-push`), agent-specific hook systems beyond the documented three forms.

## Acceptance

- Fixture repo on CI matrix: an edit event with a deliberate lint error is flagged by the validator under the Claude payload form and the argv form; a push with a failing gate command is blocked and a passing one goes through.
- Both files pass the guard and contain no project-specific defaults outside `config.json` placeholders.
