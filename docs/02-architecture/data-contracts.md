# Data contracts

File formats and conventions that more than one component depends on. Changing any of these requires an ADR update.

## Install manifest (`install/manifest.json`)

A JSON array of operations executed in order. Fields per operation:

- `op`: `copy` | `render` | `register`.
- `src`: repo-relative source path (file or dir).
- `dest`: destination with tokens (`{HOME}`, `{PROJECT}`, `{GITBASH}`).
- `scope`: `user` | `project`.
- `os`: optional list (`windows`, `macos`, `linux`); absent means all.
- `flag`: optional installer flag that must be set (for example `markitdown`, `sdd`, `testing`).
- `agent`: optional adapter id; absent means core.
- `mode`: for copy: `merge` (default, backup on overwrite) | `skip-existing`.

Both installers implement exactly these operations and nothing else; any richer logic goes into a sh tool under `tools/` invoked by an operation.

## Render tokens

- `{HOME}`: the user home in the form the destination file needs (POSIX or Windows).
- `{GITBASH}`: absolute path to `bash.exe` from Git for Windows, resolved from `git --exec-path` or `where git`; install fails with instructions if unresolvable (never falls back to `bash` on PATH, which may be WSL).
- `{BASE_BRANCHES}`: default `main,master,devel,HEAD`.

## Hook contracts

Common: exit 0 on the happy path and on any internal error (hooks never break a session); read event JSON from stdin when the event provides it; no writes outside the worklog root and the repo's `.claude/` folder; each hook is one sh file, one responsibility.

- worklog-lib (sourced, not a hook): derives `repo-id = <foldername>-<first 8 hex of md5 of the normalized repo path>`; md5 resolved per platform (`md5sum`, `md5 -q`, or `openssl md5`) with identical output; exposes worklog paths and the base-branch skip list read from git config `workflow.basebranches` falling back to `{BASE_BRANCHES}`.
- worklog-context (SessionStart): prints the current branch's task block (goal, next, open question, last snapshot) plus a one-line list of other active tasks, as additionalContext JSON. Silent when on a base branch or when no worklog exists.
- worklog-snapshot (PreCompact, SessionEnd, Stop): appends one JSON line (commit, dirty flag, timestamp, event) to `sessions.jsonl`; the Stop variant writes only when git state changed materially since the last line. Never writes semantic summaries.
- worklog-resolve (on demand from the worklog skill): prints resolved worklog paths as JSON.
- branch-check (SessionStart, project scope): compares `.claude/.active-story` with the leading digits of the branch name and prints an advisory when they differ. Read-only.
- branch-upstream-fix (SessionStart and PostToolUse on git pushes, project scope): when the current branch's upstream points at a base branch, repoints it to `origin/<branch>`. Touches only local git config; skips base branches.

## Worklog storage

`~/.claude/worklog/<repo-id>/`: `tasks/<task-key>.md` (semantic state, owned by the worklog skill) and `sessions.jsonl` (mechanical snapshots, owned by the snapshot hook). Task key is the leading digits of the branch name. Full rationale in `docs/WORKLOG.md` (F07).

## Profile contract (`CLAUDE.project.md`)

Sections every profile fills: Identity (project name, description), Tracker (kind, org/project or URL as placeholders, id format), Stack and quality gates (exact commands), Git discipline (base branch, branch naming, hand-off vs auto-commit), Testing and e2e (commands, evidence expectations), Evidence (the `Evidence root:` line, a portable spec defaulting to `{repo_parent}/evidence`), Environments. Skills must resolve every project-specific value from here; a skill containing a literal org, solution, or port is a bug (FR-031).

## AGENTS.md marked sections (project scope)

Managed sections are delimited by HTML comments `<!-- cb:<section-id> -->` ... `<!-- /cb:<section-id> -->`. The installer and adapters rewrite only inside markers. Defined section ids: `rules` (the shared working rules), `learnings` (persistent learnings, append-only, shared by all agents), `pipeline` (workflow entry points). User content outside markers is never touched.

## Evidence pack layout

The evidence root lives outside the repository, beside it, and is written as a portable spec resolved per machine by `scripts/evidence-path.py` (grammar, precedence and examples in `docs/EVIDENCE.md`; default `{repo_parent}/evidence`).

`<evidence-root>/<id>/`: `evidence.md`, `session.md`, `pr-comment.md`, `01_testings/` (test artifacts, recordings), `02_PicturesPDF/` (images, PDFs). The harness writes recordings into `01_testings/`; the evidence-report skill assembles the documents. Shared owner-facing folders sit beside the item folders under the same root, at least `<evidence-root>/mockups/`, one self-contained HTML file per visual change named `mockup-<id>-<slug>.html`.

## Capability matrix (`docs/AGENTS-SUPPORT.md`)

One table: rows are capabilities (rules, skills, workflows, hooks, subagents, worklog, evidence, statusline), columns are agents, cells are `full` | `partial` | `none` plus a footnote. Every adapter PR updates it; claims elsewhere must link here.
