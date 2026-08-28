# User stories

Acceptance criteria are the test list for the feature that claims the story.

## US-001 Install on Windows

As a Windows developer, I clone the repo and install the engine with one command, so my agent picks up the rules, skills, hooks, and statusline.

- Running the documented command on Windows 10/11 with Git for Windows installs the engine without errors.
- Hooks and statusline run on the next session start; the installer resolved the Git Bash path automatically.
- Re-running the command reports "up to date" style output and changes nothing.

## US-002 Install on macOS or Linux

As a macOS or Linux developer, I install the same engine with one command, so the setup is identical to Windows.

- The documented command works on macOS (Apple Silicon and Intel) and on mainstream Linux distros with only git present.
- The same hooks fire and the statusline shows the same fields as on Windows.

## US-003 Bootstrap a project

As a developer starting a project, I scaffold the project kit and pick a profile, so the project carries its own rules and wiring.

- One command copies the template; profiles are selectable; `--sdd` adds the spec tree and the agents.
- After filling the profile, the `/story` pipeline runs end to end without any edit to skills.
- The scaffold is committable; a teammate cloning the repo gets the same behavior with zero setup beyond the engine.

## US-004 One source of truth across agents

As a developer using several agents, I want Claude Code, Codex, and OpenCode to follow the same project instructions, so I maintain one file.

- The scaffolded project has AGENTS.md as canonical; CLAUDE.md imports it.
- Editing AGENTS.md changes the behavior of all three agents with no other edit.
- The capability matrix tells me exactly what each agent will and will not pick up.

## US-005 Codex user

As a Codex CLI user, I install the engine and get the rules, skills, and workflow prompts in Codex, so the discipline does not depend on the agent brand.

- After install, Codex sessions load the rules from its user AGENTS.md and can invoke the ported skills and workflow prompts.
- The matrix documents that hooks and statusline do not apply, and what to do instead.

## US-006 Cursor user

As a Cursor user, I get the rules (and skills and subagents where supported) inside Cursor.

- After install, Cursor applies the rules file; supported extras are installed; unsupported ones are listed in the matrix.

## US-007 Update safely

As an existing user, I update by pulling and re-running the installer, so I get fixes without losing my local edits.

- `git pull` plus re-run refreshes managed files only; my modified files get `.bak`/`.new` treatment; nothing of mine is deleted.

## US-008 Port content without leaks

As the maintainer, I port assets from private setups and the guard blocks any private identifier, so the public repo stays clean forever.

- The guard runs locally (pre-commit) and in CI on every push and PR.
- Seeding a test file with a pattern from the local denylist fails the local guard; generic patterns (personal home paths, real emails) fail in CI too.
- No committed file, including guard config, contains the private names themselves.

## US-009 Record evidence

As a developer closing a story, I record video evidence and assemble the evidence pack with the shipped harness, so the PR carries proof.

- The optional testing module runs against my app with only profile/env values (base URL, evidence root).
- It produces an MP4 demo and routes test artifacts into `01_testings/` of the story's evidence folder.

## US-010 Gate pushes in any agent

As a team lead, I wire the cross-agent project hooks so edits are validated and pushes are gated regardless of which agent made them.

- The edit-time hook lints/format-checks changed files for Claude and Copilot payloads and offers a manual mode for other agents.
- The pre-push hook blocks `git push` when the configured check command fails for commits touching the configured paths.

## US-011 Contribute an adapter

As a contributor, I add support for another agent by following the adapter contract, so the matrix grows without breaking the core.

- The adapter contract doc defines required mappings, fidelity declaration, and tests.
- A new adapter lands without modifying canonical content, and the matrix is regenerated/updated in the same PR.

## US-012 Adopt the kit in a company repository without breaking its rules

As a developer on a team whose repository already carries company rules, hooks and lint wiring, I install the kit into that repository knowing it cannot break anything, so trying it costs me nothing.

- A dry run prints the full plan before anything is written, and the plan is the whole truth: nothing else is touched.
- The repository's own `CLAUDE.md`, `.claude/` files, git hooks, `core.hooksPath`, pre-commit and lint wiring survive the install untouched; the kit version of a file I already have arrives as `<name>.new` for me to merge by hand.
- The run tells me where the backup went and the one command that puts everything back.
- One switch keeps every file the kit added out of the team's history until I decide otherwise, and the run reminds me to check the team's policy on AI tooling before committing any of it.
