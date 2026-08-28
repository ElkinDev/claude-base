# Project profile

Fill this in for the current project. It is the single place the base skills read for anything
stack- or tracker-specific. To start fast, copy the closest file from `profiles/` over this one,
then adjust. Keep it short and factual. No project code secrets here.

## Identity
- Project name: <name>
- Repo(s): <path or url>
- Integration branch: main
- Integration branch is PR-only (a direct push is rejected): yes | no

## Tracker
- Tracker: azure-devops | jira | none
- Location: <org/project for Azure DevOps, or site/project for Jira, or n/a>
- Item id format: #<digits> (Azure DevOps) | PROJ-123 (Jira) | n/a
- Commit/PR link convention: <e.g. bare #<id>, or Refs PROJ-123, or none>
- CLI: <az boards ... | jira ... | none>

## Stack and quality gates
- Frontend: <framework> in <dir>; gates: <exact commands>
- Backend: <framework> in <dir>; gates: <exact commands>
- Other components: <as needed>

## Git discipline
- Hand-off only (agent never commits/pushes/opens PRs/posts to tracker): yes | no
- Branch naming: <id>-<slug>
- Commit and PR text: Conventional Commits; no AI attribution; no em-dashes

## Testing and e2e
- Unit tests: <where and how to run>
- E2E / UI automation: <tool and location, e.g. a separate Playwright project outside the app repo>
- Demo video required for front-end changes: yes | no (if yes, how it is produced)

## Evidence
- Evidence root: {repo_parent}/evidence
- Portable spec, resolved per machine; the grammar and the per-machine override are in the kit's
  `docs/EVIDENCE.md`. One folder per item under the root, plus a shared `mockups/`.
- Task subfolders: 01_testings/, 02_PicturesPDF/

## Environments
- Local dev / test environment: <how to bring it up; never prod>
- Production policy: human-owned. Never build to deploy to prod, never test against prod.
