# Project profile: Azure DevOps + .NET / Turborepo (example)

Example profile for a team project on Azure DevOps with a .NET backend and a Turborepo/npm
frontend, hand-off git discipline, and a separate UI-automation project. Replace the placeholders
with the real values; do not commit secrets.

## Identity
- Project name: example-app
- Repo(s): C:\Repo\example-app
- Integration branch: main
- Integration branch is PR-only (a direct push is rejected): yes (Azure DevOps rejects with TF402455)

## Tracker
- Tracker: azure-devops
- Location: org <your-org>, project <your-project> (set as `az` CLI defaults)
- Item id format: #<digits>
- Commit/PR link convention: commit ends with a bare `#<id>` on its own last line; PR title starts
  with the id, `1234 feat(scope): summary`
- CLI: `az boards work-item show --id <id> --output json` (read-only)

## Stack and quality gates
- Frontend: Turborepo + npm in `src/front_end`; gates: `npm run check` (canonical), `npm run lint`,
  `npm run check-types`
- Backend: .NET in `src/back_end`; gates: `dotnet build <Solution>.sln`, `dotnet test <Solution>.sln`
- Notes: unit tests may use an in-memory DB that is case-sensitive; compare with `.ToLower()` for
  case-insensitive checks so behavior matches the case-insensitive production DB.

## Git discipline
- Hand-off only (agent never commits/pushes/opens PRs/posts to tracker): yes
- Branch naming: `<id>-<slug>`, created with `git checkout --no-track -b <id>-<slug> origin/main`
- Commit and PR text: Conventional Commits; no AI attribution; no em-dashes; lists not tables (Azure
  DevOps renders tables poorly)

## Testing and e2e
- Unit tests: `dotnet test` (backend), package test scripts (frontend)
- E2E / UI automation: Playwright in a SEPARATE personal project outside this repo, never committed
  here; outputs go under the task's `01_testings/`
- Demo video required for front-end changes: yes. Record with Playwright `recordVideo` on the real
  running app, then convert WebM to H.264 MP4 with a static ffmpeg binary (Playwright's bundled
  ffmpeg cannot x264). The human attaches it to the PR.

## Evidence
- Evidence base path: `%USERPROFILE%\Documents\Evidence\example-app\<id>\`
- Task subfolders: `01_testings/`, `02_PicturesPDF/`

## Environments
- Local dev / test environment: Docker + a local SQL container; bring it up before running tests.
  Use only designated test organizations inside the platform; never touch other tenants' data.
- Production policy: human-owned. Never build to deploy to prod, never test against prod.
