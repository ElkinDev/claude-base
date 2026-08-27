---
name: story
description: Single entry point that drives a work item through branch, plan, TDD, quality gates, and evidence. Use when starting real work on a task.
---

# Story

Single entry point for taking on a work item and running it end to end through the pipeline. This
orchestrates the existing skills; it does not replace them and it does not inline their detail.
Preserve every STOP/alignment gate below. Everything in English, no AI attribution, no em-dashes.
Anything stack-specific (tracker, gate commands, e2e tooling, evidence path, demo policy) comes from
the project profile in `CLAUDE.project.md`, not from this file.

## When to use
Starting real work on a task or work item. Invoke as `/story <id>`, or by an intent phrase (EN:
"let's start / take on / kick off / pick up story <id>", "new story <id>"; ES: "arranquemos /
tomemos / iniciemos / hagamos la story|historia|tarea <id>", "nueva story <id>").

## When NOT to use
- A one-off work-item read or a branch alignment only. Use work-item directly.
- A trivial or mechanical edit with an obvious approach and no acceptance criteria.
- Debugging an existing failure. Use investigate-issue or spec-first-debug.
- Spec-driven or greenfield work governed by a docs/ spec structure. Use /sdd.

## Pipeline (run in order, honor each STOP)

0. **Branch and evidence** run work-item. Resolve the id (from `/story <id>` args, the message, the
   current branch, or recent commits), reconcile the branch (one branch per task), and create the
   evidence folder. STOP where that skill says to ask (dirty tree, missing or degenerate title).

1. **Explore and plan** run explore-and-plan. Read the real code in the affected area, frame the
   problem and the acceptance criteria, and propose 2-3 approaches with one recommendation. STOP for
   the user to pick the approach. Skip this step only for work that already has an agreed spec, and
   say so.

2. **TDD** run tdd-workflow. Write the spec file first. STOP for spec approval. Then Test (failing
   first, specific assertions), Implement to the spec, Verify.

3. **Quality gates** run quality-gates. Use the gate commands declared in the project profile. A red
   gate means not done.

4. **End-to-end and automation** run the real running app and the project's UI/automation tests. The
   test location and tooling come from the profile (for example a separate Playwright project kept
   out of the app repo). For a front-end change, record the demo video if the profile requires one.
   Test outputs and the video go where the profile's evidence layout says. Skip the video only when
   the diff touches no front-end code, or the profile requires none, and state that.

5. **Definition of done** run definition-of-done. Confirm every acceptance criterion is met, the
   change was actually run and verified, the gates are green, and the evidence pack exists.

6. **Hand off** run evidence-report (and session-handover). Produce the evidence pack and the
   commit and PR/tracker text as plain text. Follow the project's git discipline: if it is
   hand-off-only, never commit, push, open a PR, or post to the tracker.

## Notes
- Each numbered step delegates to that named skill for the how. Do not duplicate their content here.
- Skip a step only with an explicit reason stated to the user, and name what was skipped and why. No
  silent skips.
- Keep the evidence folder and session notes current as you go, so the pack is ready at hand-off
  time, not rebuilt from scratch at the end.
