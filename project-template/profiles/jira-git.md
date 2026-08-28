# Project profile: Jira + git host (example)

Example profile for a project tracked in Jira with a GitHub or GitLab remote. Adjust the stack and
commands to the real project.

## Identity
- Project name: example-service
- Repo(s): git@github.com:your-org/example-service.git
- Integration branch: main
- Integration branch is PR-only (a direct push is rejected): yes (branch protection on the host)

## Tracker
- Tracker: jira
- Location: site your-org.atlassian.net, project key PROJ
- Item id format: PROJ-123
- Commit/PR link convention: put `PROJ-123` in the PR title and a `Refs PROJ-123` footer in the
  commit (Jira Smart Commits link it)
- CLI: `jira issue view PROJ-123` (or `acli`, or the REST API) read-only

## Stack and quality gates
- Frontend: <framework> in `web/`; gates: `npm run lint`, `npm run typecheck`, `npm test`
- Backend: <framework> in `api/`; gates: <build + test commands>
- Other components: <as needed>

## Git discipline
- Hand-off only (agent never commits/pushes/opens PRs/posts to tracker): no (the agent may commit on
  a feature branch, but never pushes to a protected branch and never opens the PR without approval)
- Branch naming: `PROJ-123-<slug>`
- Commit and PR text: Conventional Commits; no AI attribution; no em-dashes. Tables are fine on
  GitHub/GitLab.

## Testing and e2e
- Unit tests: `npm test` / the backend's test runner
- E2E / UI automation: Playwright or Cypress in `e2e/` (or a separate repo); outputs under the
  task's evidence folder
- Demo video required for front-end changes: optional

## Evidence
- Evidence root: `{repo_parent}/evidence`
- One folder per issue under the root (`<root>/PROJ-123/`), plus a shared `mockups/`. The spec
  grammar and the per-machine override are in the kit's `docs/EVIDENCE.md`.
- Task subfolders: `01_testings/`, `02_PicturesPDF/`

## Environments
- Local dev / test environment: `docker compose up` for the local stack; seed with the project's
  fixtures. Never test against prod.
- Production policy: human-owned. Never deploy to prod.
