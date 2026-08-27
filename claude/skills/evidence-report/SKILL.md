---
name: evidence-report
description: Generate and keep current a structured evidence pack for the active task. Use when wrapping up, nearing commit or PR, or asked for evidence.
---

# Evidence report

Produce a reviewable, resumable evidence pack for the task. Everything in English, no AI
attribution, no em-dashes.

## 0. When and how (continuous, not only at the end)
- Create the evidence folder EARLY, at branch creation (the work-item flow does this). Do not wait
  for "done".
- Keep session.md updated as each phase completes (spec agreed, tests green, fix in, gates passed),
  so the pack is already current by commit/PR time.
- By the time a commit message or PR comment is requested, the pack MUST already exist. Generating
  it is part of the work, not an afterthought.

## 1. Resolve the task number
In order: current git branch name (convention `<id>-<slug>`, take the leading token), the message,
recent commits (`git log`), then ask. The folder name is the id (digits, or the tracker key).

## 2. Resolve the base folder (project-aware)
The evidence base path is defined in the project profile (`CLAUDE.project.md`). Read it from there.
- If the profile defines no base, default to `%USERPROFILE%\Documents\Evidence\<project>\<id>\`,
  where `<project>` is the repo folder name.
- Never reuse a path from another project. If the base is ambiguous, ASK.
Create the folder if missing; reuse and overwrite if it exists.

## 3. Gather
- `git diff <integration-branch>...HEAD --stat` and `--name-only`
- `git log <integration-branch>..HEAD --oneline`
- impact areas; Problem / Root Cause / Fix from the conversation
- testing evidence (scripts, scenarios, before / after numbers)
- session context (decisions, approaches tried and why, open questions)
- tracker context if useful: pull the item per the profile's tracker (`az boards work-item show`,
  a Jira fetch, or nothing for plain git)

## 4. Write three files
- **evidence.md**: Problem, Root Cause, Fix, Change Details (file to change), Impact Areas,
  before-fix evidence (reference BEFORE files by name, explain what they show), after-fix evidence
  (AFTER files, before / after numbers, % gain, environments tested), Testing, Environment, Related
  Items, Open Questions, Session Context (Decisions, Approaches Tried, Resumption Notes, Commit
  History).
- **session.md**: the handover. What was done, key files table, objects touched, "How to Resume",
  chronological conversation summary, important queries run.
- **pr-comment.md**: copy-paste ready for the PR and the tracker comment. Compose it with the
  `pr-description` skill structure, which is the SINGLE SOURCE OF TRUTH for PR-text shape (this file
  holds the same content). Why-first sections, each optional: Summary, Why/Context (link the item),
  Changes, Impact areas, How to verify/Evidence (before/after with explanation), Screenshots/Demo (if
  UI), Risks/Out of scope/Follow-ups, and a `- [ ]` Checklist. Match the host's table policy (see
  `pr-description`). Do NOT invent a separate layout; run the `pr-description` skill for this file.

## 5. Front-end changes: demo video
If the diff touches the front end and the profile requires a demo, add a "Demo video" section to
pr-comment.md and remind the human to record it and attach it to the PR. You cannot record the
screen or post to the PR; you may offer to capture a short clip of the change via the verify/run
flow in a designated test environment as a starting point.

## Task subfolders
Inside the task folder, keep these numbered subfolders, created when the work happens (not only at
done) and referenced by name from evidence.md:
- `01_testings/`, validation done during the task (calculation checks, data validation), one folder
  per case: the exact runnable query or script, the input data, the captured result, and `notes.md`
  with Steps to replicate, Expected, Found, Results obtained, and Environment. Do not substitute
  "saved to memory" for these files; the human must be able to replicate or demo without the AI. If a
  finding is reusable beyond this task, also add a short memory fact pointing to the case folder
  (finding for recall, folder for proof); routine validations stay here only.
- `02_PicturesPDF/`, images and PDFs downloaded for the task (from the ticket, issue, PR, or any
  source), not left loose in Downloads or the repo.

## 6. Rules for all three
No AI attribution. Fill from conversation + git. Mark missing info as TODO (do not leave blanks). Use
today's date. Overwrite on update.
