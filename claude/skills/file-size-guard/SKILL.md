---
name: file-size-guard
description: Enforce a 300-line file size limit. Use when creating or editing files, or when a file approaches the limit.
---

# File Size Guard - 300 Line Limit

## The Rule

**Maximum: 300 lines per file**

Tolerance: 300-400 lines if splitting causes excessive refactoring.

## Before Creating/Modifying Files

1. Estimate final size
2. If > 300 lines, split BEFORE writing
3. Each file = SINGLE RESPONSIBILITY

## Naming for Split Files: MEANINGFUL NAMES ONLY

**NEVER use generic names like `_part1`, `_part2`, `_part3`.**

Every split file MUST have a name describing its content/responsibility.

```
module.{ext}      -> module_core, module_runner, module_utils
comparator.{ext}  -> comparator_core, comparator_ops, comparator_processing
reporter.{ext}    -> reporter_rows, reporter_formatting, reporter_generator
test_feature      -> test_feature_layout, test_feature_output
```

## If a File is Oversized

1. **STOP** - Don't continue adding
2. **Report** - Tell the user the file is oversized
3. **Propose split** - Suggest how to divide
4. **Get approval** - Wait for user OK
5. **Refactor** - Split the file

## Split Strategies
- Extract classes/types to separate files
- Move utilities to a `_utils` file
- Separate tests by functionality
- For docs: split by section, create an index file linking the parts

## Warning Signs

| Lines | Status |
|-------|--------|
| < 200 | Safe |
| 200-300 | Watch |
| 300-400 | Tolerance zone |
| > 400 | Must split |
