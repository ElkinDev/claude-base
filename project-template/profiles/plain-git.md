# Project profile: plain git, no tracker (example)

Minimal profile for a personal or small project with no issue tracker. The "item" is just a short id
or slug you use for the branch and the evidence folder.

## Identity
- Project name: my-tool
- Repo(s): C:\Repo\my-tool
- Integration branch: main
- Integration branch is PR-only (a direct push is rejected): no

## Tracker
- Tracker: none
- Location: n/a
- Item id format: a short slug or a local issue number (n/a if none)
- Commit/PR link convention: none, or a GitHub issue number if the host uses one
- CLI: none

## Stack and quality gates
- Frontend: <framework or n/a>; gates: <commands or n/a>
- Backend: <framework or n/a>; gates: <commands, e.g. `pytest`, `go test ./...`>
- Other components: <as needed>

## Git discipline
- Hand-off only (agent never commits/pushes/opens PRs/posts to tracker): no (the agent may commit
  directly on this project)
- Branch naming: `<slug>` or `feat/<slug>`
- Commit and PR text: Conventional Commits; no AI attribution; no em-dashes

## Testing and e2e
- Unit tests: <the project's runner>
- E2E / UI automation: optional
- Demo video required for front-end changes: no

## Evidence
- Evidence base path: optional. If you want one, `%USERPROFILE%\Documents\Evidence\my-tool\<slug>\`
- Task subfolders: `01_testings/` when a validation is worth replaying

## Environments
- Local dev / test environment: run it locally as usual.
- Production policy: n/a or human-owned as you decide.
