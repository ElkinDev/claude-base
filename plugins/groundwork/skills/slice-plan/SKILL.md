---
name: slice-plan
description: Cut a landed feature spec into ordered vertical slices with their blocking edges, and write the sprint scope and one lane brief per slice. Use when planning a sprint.
---

# Slice plan

A feature spec says what to build. This turns it into the order it gets built in: vertical slices, each one a tracer bullet that lands on its own, each declaring the slices that have to be finished before it can start.

## What it reads

- The feature spec under `docs/03-features/`, which is the source of truth for behaviour.
- The active sprint file under `docs/04-plan/sprints/`, for what is already committed to this sprint.
- `CLAUDE.project.md`, for the stack, the gate commands and the evidence root the briefs are written to.

Read the codebase too when the current shape is not already clear. Slice titles use the project's own domain words, and a slice never contradicts an ADR under `docs/02-architecture/`. Look while you are there for prefactoring that would make the rest easier, and make it the first slice: the change is easier once the ground is prepared for it.

## How to cut

A slice is a narrow but complete path through every layer it touches, from storage to interface to tests. It is not one layer of a wide feature.

- A finished slice can be demonstrated or verified by itself.
- A slice fits in one fresh context window.
- Prefactoring goes first, before anything that depends on it.
- Every slice names its blocking edges: the slices that must be finished before it can start. A slice with none can start immediately.

**A wide refactor is the exception.** A wide refactor is one mechanical change, such as renaming a column or retyping a shared symbol, whose blast radius reaches the whole codebase, so a single edit breaks call sites everywhere and no vertical slice can land green. Do not force it into a tracer bullet. Sequence it as expand and contract instead. Expand first: add the new form beside the old one so nothing breaks. Then migrate the call sites in batches sized by blast radius, one batch per package or per directory, each batch a slice blocked by the expand, and each staying green because the old form is still there. Contract last, deleting the old form once no caller is left, in a slice blocked by every migration batch. Where a batch cannot stay green alone, keep the sequence but let those batches share an integration branch, and have all of them block one final integrate-and-verify slice, which is where green is promised.

## What it writes

Two things, both files. Nothing is published to a tracker from here: the docs tree is the source of truth, and anything that has to reach a tracker goes through `work-item` (`/delivery:work-item` from the plugin channel), which reads the tracker settings from the profile.

**The sprint file.** Rewrite its scope section as an ordered list of slices, blockers first. Each entry carries a short title, its blocked-by list, and the end-to-end behaviour it makes work.

**One lane brief per slice**, under the evidence root the profile names, numbered from `01` in dependency order. Each brief carries:

- What to build, as behaviour a user can observe, never a layer-by-layer implementation list.
- Blocked by: the slices that gate this one, or a statement that it can start immediately.
- The acceptance criteria, as a checklist.

Keep file paths and code snippets out of both. They go stale faster than the prose around them. The one exception is a snippet that carries a decision more precisely than prose can, such as a state machine, a reducer or a schema shape: inline the decision-rich part of it, say where it came from, and leave the working demo out.

## Before you write

Put the proposed breakdown to the owner as one numbered block: the slices in order, with their blocking edges and what each delivers, and with your recommended granularity marked. Ask about granularity and about the edges in that same block, with a recommended answer for each, and take the answers in one pass. Then write the files.

## Then

Work the frontier: any slice whose blockers are all done can be dispatched. For a purely linear chain that is simply top to bottom.
