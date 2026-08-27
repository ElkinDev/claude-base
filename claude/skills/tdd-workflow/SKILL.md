---
name: tdd-workflow
description: Enforce the Spec, Test, Implement, Verify cycle when implementing features, fixing bugs, or writing code.
---

# TDD Workflow

Use this workflow for code changes with clear acceptance criteria.

## The Cycle

```
SPEC -> TEST -> IMPLEMENT -> VERIFY
```

### Step 1: SPEC (a document, not just chat)
Create a specification file (e.g. `specs/<feature_name>_spec.md`) including:
- **Purpose:** Why does this feature exist?
- **Data Model:** entities, fields, types, constraints
- **Functions:** for each one, name + signature, inputs with types and validation,
  output with exact structure, edge cases and error handling
- **Integration:** how it connects to existing code
- **Examples:** concrete input/output with real values

**Spec maintenance:** the spec must be saved as a file, and ALL parent/sibling specs
that reference the changed area must be updated. Spec updates are PART OF THE WORK,
not optional. A feature is NOT DONE until all specs are consistent.

**STOP and get user approval on the spec before proceeding to tests.**

### Step 2: TEST (write FIRST)
Write the test before the implementation, asserting **specific values**, not just
types. The test MUST fail first.

```
# Arrange
input_data = ...
# Act
result = function_under_test(input_data)
# Assert - SPECIFIC values
assert result == expected_value      # GOOD
# assert result is not None          # BAD - proves nothing
```

### Step 3: IMPLEMENT
Write code to pass the tests, following the spec exactly.

### Step 4: VERIFY
Run the project's test suite and quality tools (formatter, linter, type checker,
tests). All must pass before "done".

## PROHIBITED

| Don't | Why |
|-------|-----|
| Write code before the test | Violates TDD |
| Skip a test | False confidence |
| Suppress type/lint errors to "pass" | Hides real issues |
| `assert x is not None` | Proves nothing |
| `assert isinstance(x, list)` | An empty list passes |

## REQUIRED

| Do | Example |
|----|---------|
| Exact assertions | `assert len(items) == 5` |
| Real fixtures | files under `tests/fixtures/` |
| Specific values | `assert ("GET", "/api/users") in paths` |
| Output verification | read output files, check content |

## NO MOCKS UNLESS EXPLICITLY APPROVED
Mocking hides real issues. If you need to mock, ask for approval first.

## NO HARDCODED VALUES
Hardcoded values hide real issues and make tests brittle. If you need one, ask first.

## TESTS MUST VERIFY BUSINESS LOGIC (the #1 purpose)
A test that doesn't verify business logic is worthless.
- Prove the FEATURE WORKS end-to-end, not just that code runs.
- Exercise the REAL path, not a simplified mock.
- Verify OBSERVABLE OUTCOMES that matter.
- If feature A depends on B, test the chain A -> B.
- If a user reported "X doesn't work", the test must reproduce that exact scenario.

**BAD**, tests infrastructure, not logic:
```
def test_generate_report():
    result = generate()
    assert result is not None    # proves nothing
```

**GOOD**, tests logic end-to-end:
```
def test_generate_then_use_works():
    assert report_status()["exists"] is False
    generate_report()
    assert report_status()["exists"] is True
    result = find_errors("pattern")
    assert result.status_code == 200   # not 404
```

## ZERO SILENT FAILURES (absolute)
A function that fails silently is worse than one that crashes.
- Every operation that can fail MUST fail loudly: raise, return an error, or log it.
- NEVER swallow exceptions (`except: pass`, or a `try/except` that returns a default).
- NEVER trust exit codes alone, verify the ACTUAL output (exists? has content?
  correct structure?).
- In tests: a 200 OK with empty data is a SILENT FAILURE, verify content. Test the
  negative case (service down, file missing): it must fail, not silently succeed.
