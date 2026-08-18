# F12: Docs and positioning

Satisfies: FR-007, FR-025 (publication side), FR-044 (docs side). Stories: US-001 through US-011 (docs touch all). Depends on: ADR-008, ADR-009, ADR-010.

## Summary

Rewrite the public face for the multi-OS, multi-agent reality: README, INSTALL, UNINSTALL, the capability matrix page, the adapter contract for contributors, and the herdr integration note, all under the house style.

## Behavior

- README.md: what it is (the discipline kit, in one paragraph), who it is for, a quickstart per OS (clone, read, run; no pipe-to-shell), the agent support table linking `docs/AGENTS-SUPPORT.md`, the philosophy in five bullets (content as data, repo-first, safe defaults, depth over breadth, brand-light), the power-user overlay section (ADR-009), the project scaffold walkthrough, and honest prior-art credit to gentle-ai with a neutral statement of the different tradeoffs.
- INSTALL.md: per-OS requirements and steps (Windows: Git for Windows note; macOS/Linux: git only), flags reference generated from the same list F01 implements, update flow (`git pull` plus re-run), troubleshooting (Git Bash resolution, spaced homes, markitdown detection).
- UNINSTALL.md: the complete managed-file list per scope and per agent, sourced from the manifest so it cannot drift (a CI check compares them).
- `docs/AGENTS-SUPPORT.md`: the capability matrix (contract in `data-contracts.md`), plus a short adapter contract section for contributors: what a Tier 2/3 adapter must provide (mapping spec, marker discipline, smoke test, matrix column, uninstall entries).
- `docs/HOOKS.md` (from F02) and `docs/WORKLOG.md` (from F07) linked from the README docs table.
- herdr/: reconcile the folder into a single integration note (what herdr is, upstream link, config example with `~/.config/herdr` and Windows equivalents, the hotkey scripts marked Windows-only, macOS/Linux hotkey pointers); mark the whole folder optional and third-party. Whether to absorb the fuller private kit's extras stays a backlog decision; this feature only makes the current folder accurate and portable in tone.
- Rename decision: the backlog carries the repo-name question; this feature executes whatever the maintainer decided before v0.1.0 and sweeps all self-references.

## Edge cases

- Docs referencing files that move during the sprints: this feature runs last (Sprint 05) and includes a link-check pass in CI.
- Screenshots or recordings in docs: only if reproducible from shipped content; none reference private apps.

## Out of scope

A docs website, videos, localization (shipped docs are English by rule; a Spanish companion doc is a backlog idea, explicitly an addition, not a replacement).

## Acceptance

- A newcomer on each OS can go clone-to-working-engine using only README plus INSTALL (validated live per the release checklist).
- Link check green; UNINSTALL-vs-manifest check green; house-style lint green.
- The README support table matches the matrix exactly.
