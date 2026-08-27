---
name: spec-first-debug
description: Follow spec-first debugging before touching code when any test fails; the canonical spec is the source of truth, never guess.
---

# Spec-first debugging: the spec is the source of truth

When a test fails or behavior diverges from expectation, the temptation is to:
- Re-run the test as "transient" (don't).
- Loosen the assertion (don't).
- Patch the test to match observed output (don't).
- Trust a local spec file that may have drifted (verify against the canonical one).

If the project has a canonical spec store (a wiki, a docs site, a shared design
doc), that wins over any local `specs/*.md` copy, which may have been edited from
inference or written when the canonical spec said something different.

## The workflow

### 1. Read the failure output verbatim
Capture the exact error, the sample violations, and the invariant being asserted.
Record: the test name and the failing assertion; actual vs expected values, per row
if possible; the scope where the failure occurred. Do not summarize prematurely.

### 2. Find the spec that owns the rule
Locate the canonical document that defines the behavior under test. Read it. Treat
it as the source of truth over local notes.

### 3. Compare the spec verbatim to the test's invariant
Quote the relevant spec text verbatim (not a paraphrase). Decide which case applies:
- (a) The test correctly encodes the spec, so the code is wrong -> fix the code.
- (b) The test contradicts the spec, so the test is wrong -> fix the test to match
  the spec.
- (c) The local spec contradicts the canonical spec -> the local spec is wrong ->
  update it to match, then re-check (a)/(b).

**If unsure which case applies, STOP and ask.** Do not modify anything until you can
quote the spec and show how (a), (b), (c) are ruled in or out.

### 4. Investigate the root cause
Pull the actual data (or fixture data) for the failing scope, trace the code's
actual behavior on that data, and identify the exact divergence from the spec. If
the failure is infrastructure (fixture didn't load, service didn't start, bad
connection state), diagnose that with proof, never wave a failure off as
"transient" without showing the logs that prove recovery.

### 5. Fix the root cause
- Code wrong (per spec): fix it, verify, re-run.
- Test wrong (contradicts spec): update the invariant to match the spec verbatim;
  keep all strictness and scope; cite the spec in the test.
- Local spec wrong: update it verbatim from the canonical spec; drop inferred notes
  that were never in the canonical source.
- Infrastructure failed: fix the cause. Never accept silent failure.

### 6. Verify
Targeted re-run of the failed test passes, and the full suite continues without new
failures.

## Anti-patterns: STOP if you catch yourself doing these
- "It's probably transient, re-run it."
- "Let me loosen the tolerance."
- Using a local spec line without cross-checking the canonical spec.
- "There's some ambiguity, let me speculate."
- "I'll add a carve-out / footnote / implementation note" not in the spec.
- "Let me filter out the failing rows."
