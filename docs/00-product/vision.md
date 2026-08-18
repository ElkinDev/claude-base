# Product vision

## What this is

A portable engineering discipline kit for AI coding agents. It packages the working system a senior engineer applies with an agent: house rules (voice, honesty about deploy status, verification before claims), delivery workflows (story pipeline, spec-driven development, TDD, quality gates, definition of done), skills, hooks (worklog continuity, branch safety), evidence packs with video recording, a per-project template with swappable profiles, and a cross-session worklog. Claude Code is the first-class target. The same content is progressively exposed to Codex CLI (OpenAI GPT models), OpenCode, and Cursor through the AGENTS.md interchange format and per-agent adapters.

## Why

Agent setups rot in private repos: they accumulate personal paths, employer names, and business context, and they only run on one OS. This project extracts the reusable system, keeps it generic by construction (enforced by an automated sanitization guard), and makes it installable on Windows, macOS, and Linux from one public repo.

## Prior art and positioning

The gentle-ai project (https://github.com/Gentleman-Programming/gentle-ai) proved the demand for an ecosystem configurator across many agents. This project takes a different set of tradeoffs, stated without judgment:

- Content as data, not a compiled binary. Every rule, skill, and hook is a plain file in git. Updating means `git pull` and re-running a thin installer. Users can read, fork, and pin everything.
- Repo-first over global-first. The per-project kit is committed to the user's repo, so a team reproduces the exact setup from the repo alone.
- A small, deep agent matrix instead of a wide one. Full fidelity on Claude Code, documented-fidelity adapters for Codex, OpenCode, and Cursor, and a community path for the rest.
- Brand-light. No persona, no theme, no identity injection. The voice rules shipped are about professionalism, not personality.
- Delivery discipline as the core value: evidence packs, worklog continuity, and honesty gates are the features other kits do not have.

## Principles

1. Everything shipped is plain text in git. No embedded assets, no self-updating binary, no telemetry.
2. Generic by construction. No personal identifiers, no private project names, no absolute personal paths. A CI guard enforces this forever (see ADR-007).
3. Portable by contract. Every runtime script has one behavior spec and passes the same tests on Windows, macOS, and Linux.
4. Claude Code native content is the canonical source. Adapters derive other agents' artifacts from it at install time (see ADR-003).
5. Safe defaults in public, power settings as documented opt-in (see ADR-009).
6. English for everything shipped. No AI attribution anywhere. House style is part of the contract.

## Target users

- An individual developer who wants a disciplined agent setup on any OS in minutes.
- A small team that commits the project kit so every member and every agent follows the same rules.
- A developer using more than one agent (Claude Code plus Codex or Cursor) who wants one source of truth for rules and skills.
- A contributor who wants to add an adapter or a skill under clear contracts.

## Scope of v0.1.0 (first public release)

Engine install on three OSes, project template with profiles, portable hooks and statusline, the unified skill pack, evidence harness template, cross-agent project hooks, AGENTS.md interchange, Codex and OpenCode and Cursor adapters, sanitization guard, CI matrix, and rewritten docs.

## Non-goals

- Not an agent installer or version manager. It configures agents the user already has.
- Not a compiled engine. Shell-thin installers only.
- Not a 16-agent matrix. Depth over breadth; other agents arrive via community adapters.
- Not a vendor of third-party or person-attributed content. Such skills are referenced with install pointers, never bundled (see F07).
- No telemetry, no network calls beyond git.

## Success criteria

- A fresh machine on each OS goes from clone to working engine in under 5 minutes with one documented command.
- A project bootstrapped from the template is picked up consistently by Claude Code, Codex, and OpenCode reading the same AGENTS.md.
- The sanitization guard runs on every push and has zero findings on the public repo.
- CI proves hook parity on the three OSes on every change.

## Open decisions for the maintainer

- Repo name. `claude-base` is honest about the Claude-first design and is fine for v0.1. A more agent-neutral name is a possible pre-publish rename; recorded in the backlog, decide before the repo goes public.
- Whether the herdr integration folder tracks the fuller private kit or stays minimal (backlog).
