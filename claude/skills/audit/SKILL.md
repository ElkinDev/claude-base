---
name: audit
description: Audit test results against business rules and source code. Use when the user says "audit", pastes test results, or asks to review test failures. MUST be evidence-based. NO GUESSING.
---

# Audit: Evidence-Based Test Result Analysis

## Purpose
Analyze test results against BUSINESS RULES and SOURCE CODE to produce a findings
report based ONLY on confirmed root causes. NEVER guess.

## When to use
- The user says "audit".
- The user pastes test results (pass/fail output).
- The user asks to review or explain test failures, or "what's still broken?".

## Platinum rule: EVIDENCE FIRST, REPORT SECOND
All evidence (test results, code) must be thoroughly analyzed first against the
business rules. Reports must be generated from FOUND root causes, never from
guesses, assumptions, or stale information.

## Mandatory 7-step process

### Step 1: Collect evidence
Read ALL test output, every failure, every error message. Note actual vs expected
values per failure, and which tests PASSED (fixes may have landed).

### Step 2: Read the source code
Grep/read the actual scripts and source files. Check every open finding's pattern in
the CURRENT code. Do NOT rely on memory or previous sessions, read the code now.

### Step 3: Analyze against business rules
Compare evidence against the governing requirements (e.g. regulatory or domain
rules), specs, and business-logic documentation. Business logic is the SOURCE OF
TRUTH, not reports, not scripts.

### Step 4: Identify root causes with code evidence
Trace each failure to its EXACT root cause in the code. Cite specific lines or
clauses. Every root cause must have: file, line/section, what's wrong.

### Step 5: Check for fixed findings
If a finding's root cause is no longer in the code, mark it FIXED. If code changed
since the last audit, re-verify from scratch. Previous findings may be stale, never
copy blindly.

### Step 6: Update the report immediately
Mark fixed findings as FIXED with evidence. Update the failure map and pass/fail
counts. Do not defer, do it now.

### Step 7: Explain remaining failures
Only cite UNFIXED findings for remaining failures. Map every failing test to exactly
one finding.

## Absolute rules

### NEVER
- Guess at root causes. Find them in the code
- Write findings from assumptions or pattern-matching
- Copy previous findings without re-verifying current code
- Skip reading source files ("I think it's still broken" is unacceptable)
- Report a finding without citing specific code evidence
- Leave a finding "Open" when the code shows it's fixed

### ALWAYS
- Every finding has: (1) rule violated, (2) code evidence, (3) root cause
- If code changed, re-verify from scratch
- When in doubt, READ MORE CODE, never guess less
- Update report artifacts immediately, not "later"

## Output format

```
AUDIT RESULTS: [date]
Test run: X passed, Y failed

FIXED SINCE LAST AUDIT:
- [Finding]: [evidence from code that it's fixed]

OPEN FINDINGS:
| # | Finding | Severity | Tests | Root Cause (with code evidence) |
|---|---------|----------|-------|---------------------------------|
| 1 | ...     | ...      | ...   | file:line - [specific issue]    |

FAILURE MAP:
| Failed Test | Finding | Root Cause |
|-------------|---------|------------|
| test_xxx    | F-N     | [specific] |

NEXT STEPS:
- [actionable recommendations per finding]
```
