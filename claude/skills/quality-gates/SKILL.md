---
name: quality-gates
description: Run the project's quality gates (format, types, lint, build, tests). ALL must pass before declaring done. In Spanish, also trigger on "valida", "corre los checks", "pasa los gates", "esta listo para PR". Use "no tests" to skip the test gate.
---

# Quality Gates - ALL MUST PASS

Run every gate before calling work done. The exact gates and commands come from the project profile
(`CLAUDE.project.md`), because they depend on the stack. This skill is the discipline, not the
command list. If the profile declares no gates, discover them from the repo (package scripts, build
files, CI config) and ask the user to confirm before relying on them.

## How to run
1. Read the gate commands from the profile. A typical profile lists a frontend group and a backend
   group, each with the exact command and the pass condition.
2. Run each gate from the directory the profile names. Do not assume; use what the profile says.
3. Common shapes, as illustration only (use the profile's real commands):

   | Stack | Gate | Example command |
   |-------|------|-----------------|
   | Node / npm | check / lint / types / format | `npm run check`, `npm run lint`, `npm run check-types` |
   | .NET | build / test | `dotnet build <solution>`, `dotnet test <solution>` |
   | Python | lint / types / test | `ruff check .`, `mypy .`, `pytest` |
   | Go | vet / test | `go vet ./...`, `go test ./...` |

## If any gate fails
1. Fix the reported issues.
2. Re-run the failing gate.
3. Do not proceed until ALL pass.

## NEVER
- Skip gates.
- Suppress errors to bypass a gate (no `eslint-disable`, no `#pragma warning disable`, no
  test-skip attribute added just to get green).
- Lower a lint threshold or mark tests as skip.
- Ignore warnings because they are "pre-existing".

## ZERO SILENT FAILURES (absolute)
Silent failure is an invisible bug. Every operation must fail loudly.
- Connections MUST raise on failure, never return empty defaults.
- File operations MUST verify output: exists, has content, correct structure.
- API calls MUST check the response body, not just the status code.
- Config loading MUST fail if required values are missing, no silent empty defaults.
- If a test passes suspiciously fast, INVESTIGATE, it may be silently failing.
