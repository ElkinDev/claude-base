---
name: definition-of-done
description: Verify work is truly complete against seven mandatory checkboxes. Use when asked if it is done or finished.
---

# Definition of Done - MANDATORY CHECKLIST

## STOP! Before saying "done", EVERY item must be done

### The 7 Mandatory Checkboxes

| # | Checkbox | Question |
|---|----------|----------|
| 1 | Unit Tests | Do unit tests exist AND pass? |
| 2 | Integration Tests | Does it work WITH other components? |
| 3 | E2E Tests | Does it work in COMPLETE user flow? |
| 4 | Output Verification | Do E2E tests verify ALL outputs? |
| 5 | Fixture Updates | Are test fixtures updated? |
| 6 | Data Flow | Does data reach ALL components? |
| 7 | Output Artifacts | Do ALL generated files contain the data? |

## Fill This Table for EVERY Feature

```
FEATURE: [name]

| # | Status | Verification |
|---|--------|--------------|
| 1 | yes/no | [unit test files] |
| 2 | yes/no | [integration test files] |
| 3 | yes/no | [E2E test files] |
| 4 | yes/no | [outputs verified] |
| 5 | yes/no | [fixture files updated] |
| 6 | yes/no | [downstream components] |
| 7 | yes/no | [output files checked] |

VERDICT: [X]/7 complete
```

### 8. Spec Maintenance
- The feature spec MUST be saved as a file (e.g. `specs/<name>_spec.md`).
- ALL main specs referencing the changed area MUST be updated.
- A feature is NOT DONE until all specs are consistent.

### 9. Evidence pack
- The story's evidence pack must exist and be current (evidence-report skill).
- Generating or updating it is PART OF done, not after it.
- If the front end changed, the PR demo video is part of the handoff (see evidence-report).

## Rules
- < 7 checkboxes + spec maintenance = NOT DONE
- Count completed marks honestly
- If ANY is incomplete, work continues
- Don't say "done" until 7/7 + specs updated

## CRITICAL Rules - ALWAYS APPLY

### 1. REMOVE DEAD CODE
- Search for unused functions before declaring done
- Delete functions that are defined but never called
- Don't leave "backup" functions lying around
- If a function is replaced, DELETE the old one

### 2. TESTS MUST TEST THE ENTIRE PATH
- Unit tests are NOT enough
- Tests MUST verify the full call chain works
- If function A calls B calls C, test that A to B to C works
- Integration tests verify components work TOGETHER
- If testing fails to catch a bug, ADD a test for that path

### 3. ALL VERIFICATION MUST BE AUTOMATED
- NEVER rely on manual verification
- "Need to run X to verify" = NOT DONE
- "Need to check output manually" = NOT DONE
- Every verification MUST have an automated test
- If an output file needs checking, write a test that reads and verifies it

## Verification Gate - run before ANY "done" claim

Iron law: no completion claim without FRESH verification evidence. Re-run now, in full.

1. IDENTIFY the command that would prove the claim.
2. RUN it fresh and complete (no partial run, no reusing an earlier result).
3. READ the full output: check the exit code, count the failures.
4. VERIFY the output actually confirms the claim.
5. ONLY THEN make the claim.

### Red flags that mean STOP (you are about to claim without evidence)
- You wrote "should", "probably", or "seems to".
- You felt done ("Great!", "Perfect!", "Done!") before running anything.
- You are about to hand off the change as done without fresh verification.

### Three fallacies
- Confidence is not evidence.
- A clean linter is not a passing build (linter is not compiler).
- A partial run proves nothing about the whole.

### Prove the test catches the bug (regression ritual)
Write the test -> run (it passes) -> revert the fix -> run (it MUST fail) -> restore the
fix -> run (it passes). A test you never watched fail proves nothing.

## Common Failures

| Symptom | Missing Checkbox |
|---------|------------------|
| "Works locally but not in tests" | #2, #3 |
| "Data not appearing in output" | #6, #7 |
| "Test passes but feature broken" | #3, #4 |
| "Ground truth mismatch" | #5 |

## When to Use
- At START of implementation (plan what's needed)
- BEFORE saying "implementation complete"
- BEFORE saying "tests pass"
- When user asks "is it done?"
