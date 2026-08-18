# Memory discipline

Claude Code has a native file-based memory. This is the convention the base assumes; it costs no
tokens until a fact is recalled, and it keeps the always-on slice small.

## Shape
- One fact per file, with frontmatter (`name`, `description`, `type`).
- `type` is one of: `user` (who you are), `feedback` (how to work, with the why), `project` (ongoing
  work or constraints not derivable from code), `reference` (pointers to URLs, dashboards, tickets).
- An index file lists one line per fact: `- [Title](file.md) - hook`. Keep the index tight (a couple
  hundred lines at most) so the loaded slice stays small.

## What to capture
- Decisions and their reasons, conventions, and gotchas.
- Durable operational facts a future session needs but cannot derive from the committed code (who is
  on the team and their role, which environment maps to which service, how to run a local query,
  what an env var means).
- Convert relative dates to absolute before saving.

## What NOT to capture
- Anything the repo already records (code structure, past fixes, git history, the project rules).
- Anything that only matters to the current conversation.
- Replication artifacts (a runnable query, its input, its output). Those live in the task's
  evidence folder under `01_testings/`; memory holds a short fact that POINTS to that folder.

## Hygiene
- Before saving, check for an existing file that already covers it and update that instead of adding
  a duplicate. Delete facts that turn out to be wrong.
- Memory is scoped per working directory. Start Claude from the same directory each time so it
  accumulates in one scope.
