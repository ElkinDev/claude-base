# F06: Cursor adapter

Satisfies: FR-024. Stories: US-006. Depends on: ADR-003, ADR-008, F03.

## Summary

Install the discipline into Cursor: rules into Cursor's rules format, and skills and subagents where the installed Cursor version supports them, with the matrix stating exactly what carried over.

## Behavior

- Mapping spec `adapters/cursor/mapping.md` verifies at implementation time whether the installed Cursor reads AGENTS.md natively. If yes, project scope needs nothing and only user-scope rules are rendered. If no, the adapter renders the project AGENTS.md managed sections into `.cursor/rules/<kit>.mdc` with the proper frontmatter (always-apply), regenerated on installer re-run, and the file carries a header naming AGENTS.md as its source of truth.
- User scope: shared rule sections rendered into Cursor's user rules location; skills copied into Cursor's skills dir and subagent templates into its agents dir where the version supports them, per the same portable/Claude-only tagging as F04.
- Documented gaps expected: hooks, statusline, worklog automation, permission overlay.

## Edge cases

- Rendered `.mdc` drift: because this is the one place a rendered artifact lands inside a user's repo, the file header states it is generated and from what; the adapter refuses to overwrite it when the user edited it (checksum note in the header) and writes `.new` instead.
- Cursor absent at install: same skip-or-force behavior as F04.

## Out of scope

Cursor MCP configuration (backlog), anything IDE-specific beyond rules, skills, and agents.

## Acceptance

- On a machine with Cursor: the rules demonstrably influence a session; where skills/agents are supported, one of each is exercised.
- If rendering was needed: edit AGENTS.md, re-run installer, `.mdc` reflects the change; hand-edit `.mdc`, re-run, get `.new` not a clobber.
- Matrix column filled with verified values.
