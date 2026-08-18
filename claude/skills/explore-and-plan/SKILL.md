---
name: explore-and-plan
description: Front-end for new or non-trivial work. Explore the real code, frame the problem, propose 2-3 approaches with tradeoffs and a recommendation, get alignment, then hand off to TDD. Use before building a feature or a change whose approach is not yet clear. In Spanish, also trigger on "crea un plan", "planifica", "analiza la tarea/story", "como encararias". Not for trivial edits or already-specced work.
---

# Explore and Plan

Concepts before code. Use this before writing a feature or a non-trivial change, when
the problem or the approach is not yet settled. It ends by handing off to tdd-workflow.

## Step 1: Explore (no guessing)
Read the ACTUAL code in the affected area and adjacent files. Establish the current
state from the code, not from memory or a stale summary. For heavy exploration, use the
subagent-delegation skill to keep the context lean.

## Step 2: Frame the problem
State, in writing: what is being asked, why it exists, the constraints, and the
acceptance criteria. If any of these is unclear, ASK before proposing. Do not design
from an ambiguous request.

## Step 3: Propose options
Give 2-3 viable approaches. For each: the idea, the main tradeoff, and the impact on
the existing code. Then give ONE recommendation with the reason. Present neutrally, no
self-validating claims.

## Step 4: Get alignment
STOP and confirm the direction before building. The user owns the choice. Do not assume
the answer.

## Step 5: Hand off
Once the approach is agreed, move to tdd-workflow (Spec -> Test -> Implement -> Verify).
The spec captures the agreed approach. Record any durable decision or convention in
memory (keep MEMORY.md tight).

## When NOT to use
- Trivial or mechanical edits with an obvious approach.
- Work that already has an agreed spec (go straight to tdd-workflow).
- Debugging a failure (use investigate-issue or spec-first-debug instead).
