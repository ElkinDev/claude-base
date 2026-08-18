# ADR-003: Claude-native content is the canonical source

Status: Proposed (2026-07-11)

## Context

Supporting several agents needs one source of truth. Prior art hand-maintains parallel per-agent copies of the same agents and prompts, policed by contract tests, which is a known drift and maintenance cost. A fully agent-neutral intermediate format would avoid that but means rewriting all existing, proven content into an invented format.

## Decision

The existing Claude-native formats are the canonical authoring source: `SKILL.md` folders for skills, `CLAUDE.md`-style rule files with marked sections, hook scripts bound to Claude Code events. Adapters derive other agents' artifacts from this source at install time (copy where the agent understands the format, render where it needs another shape). No content is ever authored twice; an adapter that would require hand-maintained parallel content is rejected or its capability is marked unsupported in the matrix.

## Alternatives considered

- Neutral intermediate representation rendered to every agent including Claude: cleanest in theory, but an invented format, a build step for the primary target, and a rewrite of all working content. Rejected for v0.x, may be revisited if the matrix grows.
- Per-agent parallel copies with contract tests: proven to work in prior art but proven costly too. Rejected.

## Consequences

Claude Code keeps full fidelity for free and stays the reference implementation. The SKILL.md convention is already understood by several other agents, so most of the skill pack ports by copy. The limit: capabilities with no equivalent (hooks, statusline) are documented gaps, not emulations (ADR-008, FR-025).
