# Sprint 03: Unification

Goal: the best of the private setups lands here, generic by construction. Features: F07, F08, F09, plus docs/WORKLOG.md. Precondition: Sprint 01's guard is green and the maintainer's private denylist exists, because every task in this sprint ports private material.

## Scope

1. F07 skills: port `grill-me` and `check-ccs` (rewritten, sanitized); back-port the tracker improvements into `work-item` with profile-only configuration; add the coordination templates under `project-template/docs/coordination/` behind `--coordination`; write `docs/OPTIONAL-SKILLS.md`.
2. F07 worklog spec: publish `docs/WORKLOG.md` sanitized, linked from the worklog skill and `docs/README.md`.
3. F08 evidence harness: `project-template/testing/` with the seven files rewritten generically, `example.env`, evidence-root resolution through the profile, CI headless smoke scenario on the matrix.
4. F09 cross-agent hooks: the two Node templates plus `config.json`, wiring docs for Claude, Copilot, and manual mode, fixture tests in CI.
5. Roles-by-model orchestration text drafted for the template (final placement into AGENTS.md happens in Sprint 04 with F03; this sprint writes the content in the template docs).
6. Manifest and installer flags updated for the new optional modules (`--testing`, `--coordination`).

## Porting protocol (applies to every task above)

Read the private original, write the generic version fresh against its behavior (never bulk-copy), run the guard locally with the private denylist before staging, and record in the PR description which FR each ported asset satisfies. Any original that resists generalization becomes a profile example or stays private; that outcome is recorded in the backlog.

## Out of scope

Adapters, AGENTS.md restructure (Sprint 04), any new capability not mined from the private setups.

## Definition of done

- Both new skills run their happy path in Claude Code on a fixture repo; `work-item` completes with the plain-git profile and with a tracker profile using placeholders.
- Evidence harness CI smoke produces a correctly routed recording on the three OSes; PDF validation fixtures pass and fail as specified.
- Cross-agent hook fixtures green on the matrix in both payload forms.
- Guard green in CI and locally with the private denylist over the whole repo; zero waivers added this sprint without a reason string.
- New optional flags exercised by the install smoke jobs; UNINSTALL.md still matches the manifest.
