# ADR-010: AGENTS.md is the project-level canonical instruction file

Status: Proposed (2026-07-11)

## Context

A scaffolded project must feed the same instructions to Claude Code, Codex, OpenCode, and others. One private setup solved this with a canonical file plus symlinked entry stubs per agent, which works but is fragile on Windows (symlinks need developer mode; checked-out pointer files are not followed by every agent). Meanwhile AGENTS.md has emerged as the cross-agent standard location, read natively by Codex, OpenCode, and a growing list, and Claude Code supports `@` imports from CLAUDE.md.

## Decision

In the project kit, `AGENTS.md` at the repo root is the canonical instruction file, containing the shared working rules, the persistent-learnings managed section, and the pipeline entry points, in the marked-section format from `data-contracts.md`. `CLAUDE.md` is a thin file whose first line imports AGENTS.md (`@AGENTS.md`) followed only by Claude-specific notes (skill and hook references). Agents that read AGENTS.md natively need nothing else; agents that do not (for example Cursor, if native support is absent at implementation time) get a rendered rules file produced by their adapter from the same source. No symlinks, no pointer stubs.

## Alternatives considered

- Canonical hidden file plus per-agent symlinks or stubs: proven privately, but Windows-fragile and one more convention to explain. Rejected.
- CLAUDE.md as canonical with AGENTS.md generated: inverts the standard; non-Claude agents would read a generated file while the hand-edited one is Claude's. Rejected.
- Duplicate content per agent file: drift by design. Rejected.

## Consequences

One hand-edited file per project, standard location, zero Windows fragility. Claude-specific content stays possible without polluting other agents. The persistent-learnings section becomes genuinely cross-agent: a lesson captured under Codex is applied by Claude the next session.
