---
name: session-handover
description: Create handover notes for session continuation. Use at end of session or when preparing for restart.
---

# Session Handover

Create handover notes for the next session.

## Write a Handover Note

Save a handover file (e.g. `docs/handover/session_handover_[DATE].md` or wherever
this repo keeps notes) with:

```markdown
# Session Handover - [DATE]

## Completed This Session
- Task 1: [brief description]
- Task 2: [brief description]

## In Progress
- Current task: [what you were working on]
- Status: [where you left off]

## Next Steps
1. [First priority]
2. [Second priority]

## Key Decisions Made
- [Decision and why - things not obvious from code]

## Gotchas for Next Session
- [Important technical detail to remember]
```

**DO NOT include:**
- Files modified (use `git status`)
- Test status (run tests to check)
- Anything that duplicates code or git history

**DO include:**
- Context that git does not capture
- Why decisions were made
- Non-obvious technical gotchas

## Keep the Project Notes File Lean

If the repo has a long-lived notes/context file, audit it during handover and
remove anything obtainable from git or code.

**REMOVE:** file lists (use `git ls-files`), recent changes (use `git log`/`diff`),
session-by-session change logs, bug-fix histories, completed-work checklists.

**KEEP:** critical rules (testing, approval requirements), high-level project status
(ACTIVE/COMPLETE), key architecture decisions not obvious from code, important
technical notes, CLI usage examples.

**Target:** ~50-100 lines, not 200+.

## Handover Checklist
- [ ] Handover note written
- [ ] Project notes file current status updated
- [ ] Project notes file cleaned (no git-duplicate content)
- [ ] Continuation prompt provided

## Always Provide a Continuation Prompt

```
Continue working on [PROJECT_NAME].

Last session: [1-2 sentence summary]

Next: [what to do]

Read the handover note session_handover_[DATE] for context.
```
