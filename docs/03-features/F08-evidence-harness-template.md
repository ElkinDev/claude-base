# F08: Evidence harness template

Satisfies: FR-036, FR-015. Stories: US-009. Depends on: ADR-002, ADR-006, ADR-007.

## Summary

Ship the private Playwright evidence harness as an optional, fully parameterized project module: per-test video, a polished demo recorder with H.264 conversion, preflight validation, screenshot and PDF helpers, and a recording guide, wired to the evidence pack layout.

## Behavior

- Location and install: `project-template/testing/` installed under `--testing`; requires Node; the installer says so when Node is absent and skips.
- Contents, rewritten generically from the private originals: `evidence.config.ts` and `evidence-reporter.ts` (Playwright config recording 1280x720 video per test, routing artifacts into the story's `01_testings/` folder), `record-demo.mjs` (scripted demo recording, WebM to H.264 via ffmpeg-static), `preflight.mjs` (validate saved auth state and warm the target route), `shot.mjs` (single screenshot helper), `validate-pdf.mjs` (assert a downloaded PDF has real text content), `RECORDING.md` (the guide), and an `example.env` documenting every variable.
- Parameterization: base URL, ports, credentials source, auth-state path, evidence root (resolved by `scripts/evidence-path.py` from the profile's `Evidence root:` line), story id and slug come from env or CLI args; test data names are neutral fixtures. Nothing in the module names a real org, tenant, port set, or person (FR-036).
- Conventions preserved: `tests/<id>-<slug>/<case>.spec.ts` naming; artifacts land in `<evidence-root>/<id>/01_testings/`.
- The evidence-report skill's docs link to this module as the recording arm of the evidence pack.

## Edge cases

- ffmpeg-static unavailable for the platform: recorder falls back to keeping WebM with a warning.
- No saved auth state: preflight fails with instructions instead of recording a login page.
- Headless CI vs headed local: config supports both; CI smoke runs a headless no-app scenario (record a public example page) to validate the pipeline without a product app.

## Out of scope

Any product-specific test, auth automation against real identity providers, hosting recordings, non-Playwright runners.

## Acceptance

- On a clean checkout with Node: `--testing` scaffold, `npm install`, and the CI smoke scenario produce an MP4 (or flagged WebM fallback) inside a correctly resolved `01_testings/` folder on all three OSes.
- `validate-pdf.mjs` passes on a text PDF fixture and fails on an image-only one.
- Guard run over the module: zero findings.
