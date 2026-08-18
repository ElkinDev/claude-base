# ADR-008: Agent support tiers, depth over breadth

Status: Proposed (2026-07-11)

## Context

Prior art advertises 15+ supported agents and pays for it with hundreds of open issues, uneven feature coverage per agent, and constant migrations. This project's value is delivery discipline, which only works when every advertised capability actually behaves.

## Decision

Three tiers, declared in the capability matrix and nowhere else:

- Tier 1, full fidelity: Claude Code. Rules, skills, workflows, hooks, subagents, worklog, evidence, statusline, settings.
- Tier 2, documented fidelity: Codex CLI, OpenCode, Cursor. Rules via AGENTS.md or the agent's rules format, the skill pack where the agent understands SKILL.md, workflows as prompts/commands, and an explicit documented gap for everything else (hooks, statusline, worklog automation).
- Tier 3, community: any other agent, via the adapter contract (F12 documents it). Community adapters live under `adapters/` with an owner, or in forks; the core team does not maintain them.

An agent enters Tier 2 only with CI-verifiable install smoke tests and a filled matrix column. Nothing is advertised above its tier.

## Alternatives considered

- Wide matrix from day one: maximizes the README table, bankrupts maintenance. Rejected.
- Claude-only forever: simpler, but the discipline content is agent-agnostic by nature and the user base is multi-agent. Rejected.

## Consequences

The README promises less than prior art and delivers all of it. Tier promotions are deliberate events with tests, not README edits.
