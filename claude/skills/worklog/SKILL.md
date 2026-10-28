---
name: worklog
description: Maintain a cross-session worklog so any session or worktree resumes cheaply without re-reading the transcript. Save proactively at milestones.
---

# Worklog

Maintain per-task working state so the next session, or a parallel worktree, catches up from a
compact distilled note, never by re-reading the transcript. Data lives at
`~/.claude/worklog/<repo-id>/`, shared by all worktrees of the repo. The kit no longer wires its
hooks (owner 2026-10-28: they never ran); to load the current branch's task block, run
`worklog-context.ps1` by hand. This skill is the WRITE side. Everything in
English, no AI attribution, no em-dashes.

## Resolve paths first
Run this and read the JSON (RepoId, Key, Branch, TasksDir, SessionsFile, TaskFile):
```
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\hooks\worklog-resolve.ps1"
```
`{}` means not a git repo or on a base branch. `Key` is the leading digits of the branch
(`1365-portal-...` -> `1365`).

## Verbs

### save [note]
The distilled summary. Keep it SMALL (one line each for did/decisions/next), it is a summary
not a transcript.
1. Update `tasks/<Key>.md` frontmatter: `status`, `next`, `open_question`, `updated` (UTC ISO
   `yyyy-MM-ddTHH:mm:ssZ`). Touch the body (decisions, acceptance checkboxes) only if it changed.
2. Append ONE line to `sessions.jsonl` (append, no BOM, e.g. `printf '%s\n' '<json>' >> file`):
   `{"ts":"<utc>","id":<Key>,"branch":"<branch>","kind":"manual","did":"...","decisions":"...","next":"...","commit":"<short>","clean":<bool>}`

### new <id>-<slug>
Scaffold `tasks/<id>.md` (create `tasks/` if missing) with this shape, then `worklog-context.ps1`
prints it when run by hand:
```
---
id: <id>
slug: <slug>
branch: <id>-<slug>
status: active
goal: <1-2 sentences>
next: <single concrete next action>
open_question:
updated: <utc>
commit:
clean:
---
acceptance:
- [ ] <criterion>
decisions:
```
Ties to one-branch-per-task. Replaces the single `.active-story` pointer.

### done <id>
Set `status: done`, move `tasks/<id>.md` into `archive/`. Keeps the active set small.

### switch <id>
Only when not changing git branch. Normally the branch IS the switch (worktrees): use
`git checkout` or the worktree, then run `worklog-context.ps1` to print the right task.

## Save proactively, not only on /worklog save
- BEFORE asking the user a question that blocks progress: save state + the `open_question`
  first, so the next session knows exactly where it stopped. Highest value.
- On branch switch (`git checkout` to another story): save the outgoing task first.
- At milestones: tests green, commit or PR handoff.
No hook writes a mechanical snapshot any more; the commit and clean fields of each save carry
the git state. You only write the semantic save.

## Notes
- The worklog lives under `~/.claude`, never inside a project repo.
- Multiple stories run as git worktrees (one story = one worktree = one branch = one dir), all
  sharing this worklog. Per-task files + append-only sessions.jsonl means no worktree conflicts.
