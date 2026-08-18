# Non-functional requirements

- NFR-001 Dependencies: the core (installer, hooks, statusline) requires only git and the OS shell. On Windows, Git for Windows provides Bash and is already required by Claude Code, so it adds nothing new. Optional modules may require Python (markitdown) or Node (evidence harness, cross-agent hooks) and must degrade gracefully with a clear message when absent.
- NFR-002 Idempotency: every installer operation can run twice with the same result. No operation depends on execution order beyond what the manifest states.
- NFR-003 File size: every script and doc in this repo stays at or under 300 lines; split with meaningful names when approaching the limit.
- NFR-004 House style: everything shipped is English, warm, professional, direct. No em-dashes. Continuous-line paragraphs in prose. No AI attribution, no person names on shared docs, nothing named after an AI. CONTRIBUTING.md carries these rules for outside contributors.
- NFR-005 License: MIT. No bundled content under an incompatible or unclear license.
- NFR-006 Privacy: no telemetry, no analytics, no network calls from the tooling other than git itself.
- NFR-007 Performance: a full user-scope install completes in under 60 seconds on a typical machine; the statusline renders in under 300 ms per refresh on all OSes.
- NFR-008 Brand-light: no persona, no theme, no identity injection. Cosmetic defaults (statusline glyphs) are documented and easy to change.
- NFR-009 OS parity: Windows is first-class, not a port. Any behavior difference across OSes is a bug unless the capability matrix documents it.
- NFR-010 Testability: every runtime script has at least a smoke test in CI; hooks have parity tests derived from their behavior contracts.
- NFR-011 Auditability: no `curl | bash` style install is recommended anywhere. The documented path is clone, read, run. `--dry-run` shows the full plan.
- NFR-012 Reversibility: managed files are backed up before overwrite; the uninstall doc is complete; nothing writes outside the documented locations.
