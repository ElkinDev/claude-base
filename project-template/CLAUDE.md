# Working rules (base), Claude Code

@AGENTS.md

The shared working rules, the pipeline and the learnings live in `AGENTS.md`, imported above, so
every agent in this repository reads the same rules; edit them there. Claude Code skips an
`AGENTS.md` it has already loaded, so the import never reads it twice. This file holds only what
is Claude Code's own: skills, the harness's context economy, native memory and the compaction
summary. Project-specific facts stay in `CLAUDE.project.md`.

Skill names in this file are written bare, which is the form `install.ps1` leaves on a machine. A
machine that took the kit through the plugin channel gets the same five skills namespaced, and
invokes them as `/delivery:story`, `/delivery:sdd`, `/delivery:work-item`,
`/orchestration:wave-orchestration` and `/orchestration:herdr-driving`. Two more exist in that form
alone, `/groundwork:codebase-design` and `/groundwork:slice-plan`, because the installer skips their
plugin on purpose, so seven skills carry a prefix there. Read a bare name here as the installer
form, and add the plugin prefix when the machine took the plugin route.

## Skills behind the shared rules
The rules of `AGENTS.md` that a skill carries here: the branch per task (work-item skill), the
file size guard (file-size-guard), a failing test (spec-first-debug), new behavior
(tdd-workflow), the gates (quality-gates), done (definition-of-done) and the evidence pack
(evidence-report).

The pipeline of `AGENTS.md` in Claude Code: `/story <id>` (or "arranquemos/tomemos/iniciemos/
hagamos la story <id>", EN "start/take on/kick off/pick up story <id>") runs the whole chain and
keeps every STOP gate: work-item (branch + evidence) -> explore-and-plan -> tdd-workflow ->
quality-gates -> e2e + automation -> definition-of-done -> evidence-report / session-handover.
`/sdd` runs the spec-driven chain through the implementer/designer agents.

Trigger a sub-skill directly for a partial run. Delegate heavy or parallel steps with
subagent-delegation. When something breaks: investigate-issue / spec-first-debug / audit.

## Context economy (orchestrator)
- After every compaction the harness re-attaches the five files most recently touched with the Read,
  Write or Edit tools, whole, when each is under about 12 KB; a larger file comes back as a path
  reference only. Measured over one day: the re-attached files were the throwaway scripts the pane
  had just written (4 to 9 KB each, 8.2k tokens per cycle), never the briefs and reports over
  12 KB. So an orchestrator opens briefs, lane reports and evidence with the shell (`sed -n`, `cat`,
  `grep -n`); the Read tool is allowed on a file over 12 KB, which is not re-attached, and as the
  one-line read (`limit: 1`) that lets Edit work on the whole file. A lane keeps using the Read
  tool, which is the right tool for a file it is about to edit.
- The same guard runs on both routes. A text file over 48 KB, about 12k tokens, is refused unless
  the read already asks for 400 lines or fewer, and that refusal names the slice commands to use
  instead. Images and PDFs are exempt from the byte rule on both routes, because a slice of pixels
  or pages means nothing. A screenshot is still refused to this pane, with no slice offered:
  delegate the look to a lane or a fork and ask for a written description.
- Independent commands go in one Bash call separated by `;` with `echo "== label"` headers; a call
  that only looks at the previous result is merged into it; one call per question, not per command.
- Compact forms by default: `git status --short`, `git log --oneline`, `git diff --stat` before any
  hunk and then only the hunks named, `ls` without `-la` unless sizes or dates are the question,
  `grep -n` with `-m` and `cut -c` on prose, `sed -n` slices named by line. A whole file is read
  once, with the tool that will edit it, never re-read by the shell.
- Long output goes to disk, never into the context. The gradle hook already does this for builds;
  every other long tool (device logs, uploads, bench scripts, package installs) runs through
  `python scripts/hooks/run-logged.py --log <path> -- <command>` or an explicit redirect, and the
  session reads the digest and slices the log with `grep -n`.
- Records are written with the kit's record tool, never with a throwaway script:
  `python ~/.claude/tools/record.py add|amend|swap|round <target>` with the payload on stdin as a
  quoted heredoc, or `$HOME/.claude/tools/record.py` from PowerShell, where `~` stays literal. It
  keeps the file's line ending and BOM, refuses an em-dash, refuses a record already present, and
  a `round` writes every target or none. Named targets come from a
  `record.json` in the working directory or an ancestor (template: `tools/record.example.json`).
  In a heredoc longer than a couple of KB keep single quotes balanced or absent, typographic
  apostrophes in prose: the shell tool fails such a heredoc before running it.
- A long-lived orchestrating pane invokes no skill it can delegate. The lane that writes an artifact
  invokes the skill it needs (commit-message, pr-description, story, evidence-report,
  adversarial-review, work-item), and the implementer and reviewer agents preload theirs from their
  definitions. The two pipeline entry points are the exception: `/story` and `/sdd` are the chain of
  the session that runs them, invoked once at the start, and that session pays their restore at each
  of its compactions, which is a reason to keep it short-lived. A skill loaded in a pane is restored
  at every compaction of that pane, whole, for as long as the session lives: six of them measured at
  8704 tokens restored per compaction, on a pane that had already delegated the writing. A Herdr
  command is read from the herdr-driving sheet with `sed -n`, never by invoking it.

## Memory
Use the native file-based memory. Keep the index tight, one line per fact, one fact per file.
Capture decisions, conventions, gotchas, and durable operational facts a future session needs but
cannot derive from the committed code. See `docs/MEMORY.md`.

## Compaction instructions

When this conversation is compacted, the summary must keep verbatim: the acceptance list of the work in flight; every decision taken and every ruling from the owner, quoted; the next step and who owns it; the agent reports received and the board line per branch, and the path of the session brief; the branch and tip of every worktree touched; the last gate verdict; every ban in force. Drop tool outputs, build logs and intermediate reasoning: the files are the record, not the summary.
