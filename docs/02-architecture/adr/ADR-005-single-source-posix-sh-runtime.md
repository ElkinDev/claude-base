# ADR-005: Runtime scripts are single-source POSIX sh

Status: Proposed (2026-07-11)

## Context

The hooks and the statusline currently exist only as PowerShell, so nothing runs on macOS/Linux. The options are maintaining ps1+sh pairs forever, or one portable implementation. Claude Code on Windows already requires Git for Windows, which ships Bash, so a POSIX interpreter is guaranteed present on every supported machine.

## Decision

Core runtime scripts (the six worklog and branch hooks plus the statusline) are written once in POSIX sh. On macOS/Linux they run natively; on Windows the installer wires them through the absolute path to Git Bash resolved at install time (never the bare `bash` on PATH, which can resolve to WSL). The existing PowerShell versions stay only until CI parity tests prove the sh versions on all three OSes, then they are removed (Sprint 02). Exceptions, by declared scope: `markitdown-read.py` stays Python (optional feature, Python is its dependency anyway), the cross-agent project hook templates are Node (they must run identically under multiple agents and dependency-free JSON handling in sh is not worth it), and Windows-only conveniences like the hotkey setup stay PowerShell.

## Alternatives considered

- Permanent ps1+sh pairs with a parity contract: proven pattern but permanent double maintenance and drift risk. Rejected as an end state, accepted as the migration overlap.
- pwsh everywhere: single language but adds an install dependency on macOS/Linux. Rejected.
- Node or Python hooks: adds a core runtime dependency (NFR-001). Rejected for core, accepted for the optional modules named above.

## Consequences

One implementation, one behavior contract (in `data-contracts.md`), parity tests instead of parity by hand. Risks accepted: sh spawn latency on Windows for the statusline (must stay under NFR-007's 300 ms) and CRLF/quoting discipline (enforced by `.gitattributes` forcing LF on `*.sh` and by shellcheck in CI). If Git Bash resolution fails at install time, the installer fails with instructions rather than silently degrading (FR-004).
