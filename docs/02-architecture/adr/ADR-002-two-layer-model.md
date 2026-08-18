# ADR-002: Two layers, engine and project kit

Status: Proposed (2026-07-11)

## Context

Some setup belongs to the person (voice, honesty rules, worklog, statusline) and some to the repo (stack, tracker, gates, git discipline). Prior art defaults to global-first installs, which makes team reproducibility depend on every member's machine state.

## Decision

Keep the existing two-layer model. The engine installs once per machine into user scope. The project kit is scaffolded into each repo, is committable, and carries every project-specific value through exactly one profile file (`CLAUDE.project.md`). Skills and hooks resolve project specifics only through the profile.

## Alternatives considered

- Global-only: simplest install, but teams cannot reproduce a setup from the repo. Rejected.
- Project-only: everything per repo, but personal rules and the worklog would be duplicated and drift per project. Rejected.

## Consequences

A repo plus the engine fully determines behavior. The profile contract in `data-contracts.md` becomes load-bearing: a skill hardcoding an org, port, or path is a bug by definition (FR-031).
