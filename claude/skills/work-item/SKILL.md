---
name: work-item
description: Read a work item from the project's tracker (Azure DevOps, Jira, or git) and align the git branch to it. Read-only; hands off updates.
---

# Work item

Read or search a work item / issue on the project's tracker and keep the git branch aligned to
it. The tracker is not hardcoded: it is declared in the project profile (see the project's
`CLAUDE.project.md`). Everything in English, no AI attribution, no em-dashes.

## 0. Read the tracker from the profile
The active profile names one of:
- `azure-devops`, work items via `az boards` (org and project set as CLI defaults or in the profile).
- `jira`, issues via a Jira CLI (`jira`, `acli`) or the REST API; keys look like `PROJ-123`.
- `none` (plain git), no tracker. The "item" is just the id or slug you use to name the branch.

If the profile does not declare a tracker, ask once, then proceed as `none`.

## 1. Golden rules (do not break)
- The tracker is READ-ONLY in this skill: read, search, list. Never transition state, never add a
  comment or update. Writing to the tracker is outward-facing and belongs to the human.
- Never mutate git silently. `git checkout` and branch creation are allowed, but if a switch or
  create is implied, follow the reconcile flow in section 3 and STOP where it says to ask.
- Hand off text, never post it. For a comment or status note, produce the text (reuse the
  `evidence-report` pr-comment shape) and stop.

## 2. Resolve the item id
In order: the current message, the current branch (`<id>-<slug>`, take the leading token), recent
commits (`git log --oneline`), then ask. Azure DevOps ids are digits (e.g. 1365); Jira keys are
`PROJ-123`; plain git can use digits or a short slug.

## 3. Read the item (per tracker)
- **Azure DevOps:**
  ```
  az boards work-item show --id <id> --output json
  ```
  Useful fields: `System.WorkItemType`, `System.State`, `System.Title`, `System.Description`,
  `Microsoft.VSTS.Common.AcceptanceCriteria`. Add `--expand all` for relations (parent, linked PRs).
  Search: `az boards query --wiql "SELECT [System.Id],[System.Title],[System.State] FROM workitems WHERE [System.AssignedTo] = @me AND [System.State] = 'Active'"`.
  If a command returns nothing, auth likely expired: tell the human to run `az login` then
  `az devops login`. Do not retry in a loop.
- **Jira:**
  ```
  jira issue view <PROJ-123>
  ```
  (or `acli jira workitem view`, or a REST GET to `/rest/api/3/issue/<PROJ-123>`). Useful fields:
  type, status, summary, description, acceptance criteria. If auth fails, tell the human to
  re-authenticate the Jira CLI. Do not loop.
- **Plain git (`none`):** there is nothing to read. Take the id/slug from the message and go to
  section 4.

## 4. Reconcile the branch (one branch per task)
Derive the slug from the item title (deterministic, no confirmation needed): lowercase, replace each
run of non-alphanumeric characters with a single hyphen, trim and collapse hyphens, cap at the first
6 words or ~50 chars on a word boundary. Branch = `<id>-<slug>`. Ask about the slug ONLY if the
title is missing or the rule yields a degenerate slug. The integration branch and whether it is
PR-only come from the profile (default: `main`, PR-only).
1. `git rev-parse --abbrev-ref HEAD`. If the current branch already carries this id, proceed.
2. Else if a branch matching the id exists AND the tree is clean (`git status --porcelain` empty),
   check it out.
3. Else if the tree is dirty, STOP and tell the human. Never switch over uncommitted work.
4. Else (no branch yet) run the new-task flow:
   - derive the slug by the rule above;
   - `git fetch origin`;
   - `git checkout --no-track -b <id>-<slug> origin/<integration-branch>` (never track the
     integration branch when it is PR-only; a plain push to a protected branch is rejected, e.g.
     Azure DevOps TF402455);
   - if it was created tracking the integration branch by mistake:
     `git config branch.<id>-<slug>.merge refs/heads/<id>-<slug>` (the `branch-upstream-fix` hook
     also repoints it on the next git call);
   - write the id to `.claude/.active-story` (keep it out of version control) so the `branch-check`
     hook stays aligned;
   - set up the evidence folder (see the `evidence-report` skill).

## 5. Hand off
When the task is done, generate the evidence pack (`evidence-report`). The pr-comment file is the
copy-paste text for both the PR and the tracker comment. The human posts it; you do not.
