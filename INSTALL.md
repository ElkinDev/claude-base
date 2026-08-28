# Install

## 0. Check the machine first
```
python scripts/doctor.py
```
One line per check, `ok | warn | FAIL`. It exits 1 only when a required tool is missing (git,
Python 3.8+, Claude Code, and on Windows the Git Bash the hooks run through), so fix every FAIL
before installing. Herdr is optional: when it is absent you get a warning and nothing else, and
when it is present the doctor also probes the subcommands this kit drives. `--json` prints the same
checks for a script.

## 1. User-level engine
Installs the skills, hooks, and status line into `%USERPROFILE%\.claude` (or `$KIT_HOME` when you
set it), and sets up `settings.json`. See the plan first, then run it:
```
powershell -ExecutionPolicy Bypass -File .\install.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File .\install.ps1
```
Nothing you already have is overwritten or deleted. Any file the installer did not write itself is
backed up, kept exactly as it is, and the kit version lands beside it as `<name>.new` for you to
merge by hand. If you already had a `settings.json`, that means `settings.json.new`: keep your own
keys and add the `statusLine` block and the `worklog` SessionStart / PreCompact / SessionEnd / Stop
hooks, and the compaction hooks (PreCompact checkpoint with summary steering, PostCompact summary
persistence, SessionStart recovery; see `docs/CONTEXT-ECONOMICS.md`).

Requirements: Windows PowerShell 5.1+, `git` on PATH, and a terminal font with emoji for the status
line glyphs.

## 2. A project
Scaffold a project so it gets the working rules, a profile to fill, and the branch hooks:
```
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Project C:\Repo\my-app
```
Adopting the kit in a repository that already has rules of its own (a company `CLAUDE.md`, git
hooks, pre-commit or husky wiring) is a case of its own, and `docs/ADOPTION.md` covers it end to
end: what the run touches, what it never touches, how to keep every kit file out of the team's
history with `-LocalOnly`, and how to roll the whole thing back. Read it before you install into a
repository you share.
Then pick a profile: copy the closest `project-template\profiles\*.md` over `my-app\CLAUDE.project.md`
and fill in the blanks (tracker, gate commands, integration branch, evidence root, git discipline).

The evidence root is a portable spec, not a path: it defaults to `{repo_parent}/evidence`, so
evidence sits beside the repository and is never committed. A machine that already keeps evidence
somewhere else writes one `Evidence root:` line in `CLAUDE.local.md`. See `docs/EVIDENCE.md`, and
check what a project resolves to with `python scripts\evidence-path.py --print-spec`.

For a spec-driven project (a new build governed by a `docs/` spec structure), add `-Sdd` to also
scaffold `.claude/agents/` and the `docs/` spec structure, then drive it with `/sdd`:
```
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Project C:\Repo\my-app -Sdd
```

The installer copies the agents folder by wildcard, so the project gets all five templates:

- `analyst` decision-shaping analysis with file:line citations, read-only toward the repo
- `designer` UI and design deliverables from the design brief and the feature specs
- `implementer` implements a feature or sprint slice strictly from the written specs
- `implementer-light` the same at lower effort, for mechanical slices (tests, strings, docs, renames)
- `reviewer` adversarial review of a branch diff before merge; a disposition, never a fix

If you keep the project rules local (not committed), re-run the project command with `-LocalOnly`:
it appends exactly the paths that run manages to the exclude file git actually reads, which is not
always `.git/info/exclude` (in a worktree or a submodule it lives in the common directory, and the
installer asks git for it). To do it by hand, append to the file `git rev-parse --git-path
info/exclude` prints:
```
CLAUDE.project.md
.claude/
```

## 3. Herdr and the global hotkey (optional)
See `herdr/README.md`. In short:
1. Install Herdr; copy `herdr/config.toml` to `%APPDATA%\herdr\config.toml`
   (`~/.config/herdr/config.toml` on macOS/Linux).
2. Run `herdr integration install claude` so Herdr tracks Claude Code sessions.
3. Install the Ctrl+Alt+N hotkey:
   ```
   powershell -ExecutionPolicy Bypass -File .\herdr\hotkey\setup-hotkey.ps1
   ```

## 4. Another device (clone and go)
1. Clone this repo and run `python scripts\doctor.py`.
2. Run `install.ps1` (user scope).
3. Copy `herdr/config.toml` into place and run `herdr integration install claude`.
4. Run `herdr/hotkey/setup-hotkey.ps1`.
5. Review the defaults in `settings.json` (permission mode, model, effort) and adjust.

## Guard before you push
This repo carries a guard that blocks a push carrying a personal home path, a real email address, a
tracker org URL, a credential shape, or a private name from your local denylist. Install it once per
clone:
```
python scripts\sanitize-check.py --install-hook
```
Add `--pre-commit` to check what a commit is about to record as well. Everything runs locally;
nothing is sent anywhere.

On a push the hook scans every commit the push would publish: each commit message, every path those
commits add or modify, the contents behind those paths, and the names of the refs being pushed. A
file that was added and deleted again inside the range is still scanned, because it stays in the
history the push publishes, and every path is name-scanned even when several of them carry the same
content, so renaming a file to something clean does not hide the name it had.

Pushing a tag scans the tag itself, all of it: the message, and the header block with the tagger
name and address, which travel with the tag. Then whatever the tag points at is scanned in turn, a
tag at a time down to what it finally names. A tag can point straight at a file or at a whole tree
without any commit behind it, and those are scanned too, contents and names both. Every entry under
a tree is name-scanned even when there is nothing to read behind it, an empty subdirectory or a
submodule, because the name is the whole of what such an entry publishes.

`--all` scans every tracked file, `--staged` scans the index, and a path argument scans just that
file or folder. Names are scanned as well as contents, so a private term in a file or directory name
is caught. Findings print as `<rule-id>: <file>:<line>` with the matched fragment; the exit code is 0
when clean, 1 with findings, and 2 on a usage error or a hard failure, which includes a path that
does not exist and git refusing to answer. A reader that closes the pipe partway through the report,
which a pager or a `head` does routinely, ends the run at 1 with no traceback.

The output is meant to be publishable, so nothing private is echoed anywhere in it. A finding from
the denylist prints its rule id and location and no fragment. A generic finding on a line that also
carries a private name prints `redacted` instead of its fragment. A file or folder whose name
carries one is reported as `private-path: <redacted path>`. Waiver reasons are never printed. The
`.sanitize` folder is never scanned in any mode, and a path argument pointing into it is a hard
failure rather than a scan.

A push is the one place where a path under `.sanitize` is not skipped but rejected. A commit that
carried the denylist keeps carrying it after `git rm --cached`, so the working tree looks clean while
the push would publish every term. Finding one among the objects a push or a tag would publish is a
hard failure that names the commit and the path, never the contents, and the remedy is to rewrite
the history before pushing.

The generic rules are committed in `scripts\sanitize-rules.txt` and name nothing. The real private
names go in `.sanitize\private-denylist.txt`, one term or `regex:` line each. That file is
gitignored, never leaves the machine, and a finding from it prints only the rule id and the location,
so the guard's own output stays publishable. Keep a private backup of it, because it is deliberately
not in the repo, and note that anything tracked under `.sanitize\` makes the checker fail hard.

A literal term on the denylist matches across the shapes a name is written in: a CamelCase hump, a
digit and an underscore all count as boundaries, so one term covers `AcmeViewModel`, `ACME_API_ID`,
`acme-app`, `XAcmeClient`, `v2AcmeClient`, `ACMEapp` and `ACMEApp` without also matching `acmeish`.

One shape it deliberately does not catch is an all-caps term glued to more capitals, `ACMEAPP` or
`MYACME`. There is no boundary that separates those from an ordinary word, since `CACHED` carries
`ACHE`, and a rule that matched them would fire on unrelated text. An all-caps term glued to a
capitalised word is caught, `ACMEApp` and `ACHEPoint`, because the capital that starts the next word
is a boundary an ordinary word does not have. If your project uses a glued constant, add an explicit
`regex:` line for it to the denylist.

A `regex:` line is refused if it quantifies a group that already carries a quantifier, `(a+)+` and
its relatives. Python's regular expressions have no timeout, and that shape backtracks long enough
on an ordinary line to hang the hook and the push with it. The refusal names the rule number and
never the pattern; rewrite it as an alternation or as a plain literal term. The second shape to
avoid is an alternation whose branches overlap, `(a|a)+` and its relatives, which backtracks the same
way for the same reason and which nothing can refuse for you, since no static check can tell two
overlapping branches from two useful ones.

Sanctioned placeholders live in `scripts\sanitize-allow.txt`. An entry there clears a finding only
when it full-matches the name the rule captured, which is the user in a home path, the domain in an
address, or the org in a tracker or forge URL. A rule that captures no name, which is every
credential shape, can never be cleared that way.

The hook fails closed. A missing checker, or no Python 3.8 or newer (it tries `py -3`, `python`,
`python3` in that order), blocks the push instead of waving it through.

A false positive gets a waiver comment on its line, `sanitize-ok: <rule-id> <reason>`. Waivers are
counted and listed in the summary, so they stay visible instead of quietly accumulating; the reason
is for whoever reads the file, not for the output, which never prints it. The waiver id `all` is the
whole-file one: on the first non-empty line of a file under a `fixtures` directory it skips the
generic rules for that file and the summary says how many files were skipped that way. Anywhere else, or
below the first line, it is reported as `waiver-abuse` and the file is scanned anyway, which is what
keeps it from becoming a way to silence the guard.

No waiver of either kind suppresses your denylist, which reads every line of every file, fixtures
included. Writing `sanitize-ok: private-2` on a line is itself reported as `waiver-abuse` and the
finding still stands: a waiver says a generic rule matched something harmless, and that is not a
claim anyone gets to make about one of your own names.

If the repo already manages its hooks, through `core.hooksPath` or a hook manager, the installer
writes nothing and prints the line to add by hand. That line is:
```
sh "$(git rev-parse --show-toplevel)/scripts/git-hooks/pre-push" "$@"
```

## When Herdr updates
Herdr is a preview build and its CLI moves. The kit drives a small surface of it from
`claude\claude-account.ps1`, `scripts\compact-at-boundary.py`, `claude\hooks\landing.py` and
`claude\hooks\alarm-big-result.py`, so a renamed subcommand breaks those silently.

After every Herdr update run `python scripts\doctor.py`. A `warn herdr` line means the installed
version differs from the one the kit was verified against; a `FAIL herdr <subcommand>` means that
subcommand is gone or renamed. For each FAIL, read the new `herdr <subcommand> --help`, fix the code
that drives it, then update `herdr\cli-surface.txt` and `herdr\verified-version.txt` in the same
commit as the fix. The `herdr-driving` skill holds the verified behaviour of each call.

## What the installer does not touch

It deletes nothing, ever, in either scope, with or without `-Force`. It knows which files are its
own because it records a sha256 per file in `<kit home>/.kit-manifest.json`, and it treats
everything else as yours.

- A file that matches its manifest record is a kit file nobody changed, so a re-run refreshes it,
  after copying it into `<kit home>/backups/<stamp>/`.
- A file that differs from the record, or that the manifest never heard of, is yours. It is backed
  up, left exactly as it is, and the kit version is written beside it as `<name>.new` for you to
  merge. `-Force` overwrites it instead, still after a backup.
- A file whose content already matches is skipped in silence, so a re-run of an up-to-date install
  writes nothing at all, not even the manifest. The one thing such a run does change is a record
  that was lost: a file that is byte for byte the kit's goes back into the manifest, because
  without the record a rollback would no longer be allowed to remove it.
- `-DryRun` prints the full plan, one line per file with its action, and writes nothing.
- In a project it writes only `CLAUDE.md`, `CLAUDE.project.md` and `.claude/`, plus `docs/` on the
  runs you pass `-Sdd` to, and it names those paths in the preflight before it plans anything. It
  never writes a git hook, never sets `core.hooksPath`, and never adds a CI file. Existing husky,
  pre-commit, lefthook, CODEOWNERS and editorconfig wiring is listed in the preflight and then
  left alone.
- `-LocalOnly` (project scope) appends the paths that run manages inside the repository, the `.new`
  proposals included and a file of your own never, to the exclude file git reads for that working
  tree, without ever duplicating a line. Everything under `.claude/` goes in as the single folder
  line `.claude/`, so whatever the team later puts in that folder is hidden with it.
- It does not write the Herdr hook (`herdr-agent-state.ps1`); Herdr's own integration owns that file.

Every run that wrote anything ends by naming its backup folder and the command that reverses it:
```
python scripts\kit-restore.py --list
python scripts\kit-restore.py --stamp <stamp>
```
A run killed from outside, by Ctrl+C at the console or by `Stop-Process`, never gets to print that,
but the backup folder holds every line it had written by then, `--list` shows it with the files it
holds, and rolling that stamp back puts them back. The copies carry your bytes, not your Windows
file attributes: a read-only file comes back writable.
`docs/ADOPTION.md` has the full story, including the manual rollback list.
