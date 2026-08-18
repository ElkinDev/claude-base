# ADR-009: Safe permission defaults, power mode as local opt-in

Status: Proposed (2026-07-11)

## Context

The current `settings.json` ships the author's personal working mode: permissions bypassed and the dangerous-mode prompt skipped. That is a defensible personal choice and an indefensible public default: a newcomer installing a toolkit must not silently inherit an agent that executes without asking.

## Decision

The shipped `settings.json` uses the agent's standard permission prompting, adds a deny list for secret material (SSH keys, key files, `.env*`, cloud credentials), and sets only uncontroversial conveniences (statusline, hooks, theme). The README documents the power overlay: exactly what the author runs, why, and how to enable it in the user's own `settings.local.json`, framed as at-your-own-risk. The installer never writes the power overlay.

## Alternatives considered

- Ship the personal defaults with a README warning: warnings do not survive quickstarts. Rejected.
- Interactive prompt at install ("choose your mode"): friction and state; a documented overlay achieves the same with less machinery. Rejected for v0.1, may become an installer flag later.

## Consequences

Fresh installs are safe by default and slightly less convenient; the author and other power users add one documented file. Model and effort defaults also move to the documented overlay, since they are personal choices too.
