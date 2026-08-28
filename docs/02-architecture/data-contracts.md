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

## Managed-file manifest (`<kit home>/.kit-manifest.json`)

The record that lets an installer tell its own files apart from the ones the user or the company wrote. Every installer port writes and reads exactly this file, so a tree installed by one of them can be updated or rolled back by another (F13, F01).

```
{ "version": 1, "installed": "2026-08-28T10:15:00", "files": { "<absolute path>": "<sha256 at install>" } }
```

- Kit home: `$KIT_HOME` when the variable is set, otherwise `~/.claude` (`%USERPROFILE%\.claude` on Windows). The installer, the restore tool and the tests all resolve it the same way.
- `files` holds one lowercase hex sha256 per absolute path, for user scope and project scope alike, merged with the previous run rather than replaced. Paths compare the way the host OS compares them (case-insensitively on Windows).
- Written as UTF-8 with no BOM, and only when the file map actually changed, so a re-run that writes nothing leaves the tree byte for byte as it was.
- Anything that is not this engine's map of absolute path to hash counts as broken and is read as "nothing is managed": bad syntax, an empty file, a file holding only a BOM, valid json that is an array (a one element array wrapping the object included, which some json readers hand back with the object's own members still answering), a missing `version`, a `files` value that is not an object, an entry whose value is not a string, and a key that is not a whole path. Whole means a drive with a separator (`C:\...`) or a UNC share (`\\server\share\...`); a key like `\kit\x.md` or `C:x.md` is rooted but still names a different file depending on where the run started, so it counts as broken like any relative key. A port says so, moves the file aside to `.kit-manifest.json.broken-<stamp>` and starts a fresh record; it never deletes what it could not read.
- A `version` a port does not know is broken too, not something to guess at: 1 is the only shape defined here, so a port that meets any other number treats the record as unreadable, keeps the `.broken-<stamp>` copy and starts a fresh record of its own. The copy is what makes that recoverable, by hand or by a later port that does know the version.
- Ownership rule every port implements: target absent, write and record; target present with the recorded hash, refresh after a backup; target present with the incoming bytes, write nothing and record it, which is how a lost record repairs itself; target present with any other content, or absent from the manifest, back it up, keep it, and write the incoming version as `<name>.new`. `--force` overwrites that last case, still after a backup. Nothing is ever deleted.
- The `<name>.new` proposal is itself a managed file: it is recorded in `files` and goes through the same rule, so a draft of the user's own sitting at that name is backed up before it is replaced, and a re-run that finds the proposal already holding the incoming bytes writes nothing at all.

## Backup layout (`<kit home>/backups/<stamp>/`)

One folder per run that recorded anything, named `yyyyMMdd-HHmmss`, with `-2`, `-3` appended when a folder of that name already exists, so two runs inside the same second never write into one folder. Inside, each replaced file sits under its original absolute path with the drive letter as a folder, so a file at `C:\<rest of its path>` is kept at `<backup root>\C\<rest of its path>`, and `manifest.txt` records the run as one verb per line followed by the original absolute path:

- `trigger <text>`: what wrote it, for example `user scope install`. First line, one per folder.
- `overwritten <path>`: the previous content is in this folder, at the mirrored path.
- `created <path>`: the file did not exist before this run, so a rollback removes it.
- `new <path>`: a `.new` proposal written beside a file the installer refused to touch. A rollback names it and leaves it in place.
- `kept <path>`: a file the run deliberately did not write because it is the user's. The copy is in the folder, but a rollback only names it: there is nothing to undo.

Every port appends each line and flushes it before the write it describes, and creates the folder on the first line, so a run that dies halfway leaves a record that describes exactly what it had done, and a run that changes nothing leaves no folder at all. That holds for a run killed from outside as well, which never reaches the managed-file manifest: the lines are on disk, and they are then the only record of the run. Copies carry bytes, not file attributes.

`scripts/kit-restore.py` is the reader: `--list`, then `--stamp <stamp>` to reverse one run. A folder whose `manifest.txt` holds no line it understands, and a stamp folder with no `manifest.txt` at all, are argument errors (exit 2) answered in words, never a silent rollback of nothing. An entry the managed-file manifest never got is restored from the copy in the folder, and a `created` entry in that state is removed only after a copy of it goes into the folder. Backup folders are never deleted by the tooling; they are the user's to prune.

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
