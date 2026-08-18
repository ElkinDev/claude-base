# F05: OpenCode adapter

Satisfies: FR-023. Stories: US-004, US-005. Depends on: ADR-003, ADR-008, F03.

## Summary

Install the discipline into OpenCode: user-scope rules into OpenCode's global AGENTS.md location as managed sections, the portable skill pack via OpenCode's skill or command mechanism, and the workflow entry points as slash commands.

## Behavior

- Mapping spec `adapters/opencode/mapping.md` verifies destinations against the installed OpenCode version at implementation time: global rules file (managed sections, merge-preserving), commands for the workflow entry points, and skills through whichever of OpenCode's mechanisms (native SKILL.md resolution, command wrappers, or a config merge into `opencode.json`) carries them with the least machinery. The spec picks one mechanism and records why.
- Any `opencode.json` merge is key-scoped: the adapter owns only its named entries and never rewrites the rest of the file.
- Project scope needs nothing: OpenCode reads the project AGENTS.md natively (F03).
- Documented gaps expected: hooks and statusline (OpenCode has a plugin system that could host a worklog equivalent; that is a backlog item, not part of this feature), permission overlay.
- Skill compatibility uses the same portable/Claude-only tagging from F04.

## Edge cases

- OpenCode absent at install: same skip-or-force behavior as F04.
- Existing `opencode.json`: config merge must be additive and reversible; a malformed existing file aborts the merge with a clear message rather than clobbering.
- OpenCode's config schema drift: mapping spec records the verified version; CI smoke pins it.

## Out of scope

A TypeScript worklog plugin (backlog), MCP configuration (backlog), theme or persona of any kind.

## Acceptance

- On a machine with OpenCode: rules visibly applied, one ported skill invoked, one workflow command runs its happy path.
- `opencode.json` round-trip test: install, uninstall by removing managed entries, file equals the original.
- Matrix column filled with verified values.
