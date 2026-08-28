# Evidence

Evidence is everything that proves a piece of work happened and can be replayed: the scripts and
queries a validation ran, the recordings and screenshots, the reports handed to a reviewer, the
mockups shown before a visual change. It is not source code, and it does not live in the repository.

## Why outside the repository

Evidence lives beside the repository, never inside it. Four reasons, in the order they bite:

- It is never committed by accident. A recording dropped into the working tree is one `git add -A`
  away from the history, and video does not come out of a history cleanly.
- No ignore rules to maintain. Nothing has to be added to `.gitignore` or to `.git/info/exclude`, so
  nothing is forgotten on the day a new subfolder appears.
- It survives the worktree. A branch's worktree is removed the moment its work merges; evidence kept
  inside it would go with it, and evidence is most useful after the merge, not before.
- The same layout in every project. One convention, resolved per machine, so a skill never carries a
  path and a reader never wonders where to look.

## The layout

For a repository at `<parent>/<repo>` the default evidence root is `<parent>/evidence`:

```
<parent>/
  <repo>/                     the repository
  evidence/                   the evidence root
    1234/                     one folder per work item, named by its number or key
      evidence.md             problem, root cause, fix, impact, before and after
      session.md              the handover: what was done, key files, how to resume
      pr-comment.md           copy-paste text for the PR and the tracker comment
      01_testings/            test artifacts and recordings, one folder per case
      02_PicturesPDF/         images and PDFs pulled in for the item
    PROJ-77/                  a tracker key works the same way
    mockups/                  owner-facing shared folder, one file per visual change
      mockup-1234-export-dialog.html
```

Two kinds of folder sit under the root. Per-item folders are named by the work item id: the digits
of an Azure DevOps item, a Jira key such as `PROJ-77`, or the slug a plain-git project uses for the
branch. Shared folders are named by what they hold, and there is at least one of them, `mockups/`,
which carries one self-contained HTML file per visual change, named `mockup-<id>-<slug>.html`, so a
change can be approved or rejected on the picture before any implementation starts.

The three documents and the two numbered subfolders are the evidence pack, defined in
`docs/02-architecture/data-contracts.md` and assembled by the `evidence-report` skill.

## The spec grammar

A machine that already keeps evidence somewhere else keeps it there. So the root is written as a
portable spec, never as a literal path, and it is resolved at run time on the OS at hand.

A spec is a string with forward slashes and these tokens:

| Token | What it resolves to |
|---|---|
| `{repo_parent}` | the folder that contains the repository |
| `{repo}` | the repository folder itself |
| `{repo_name}` | the name of the repository folder |
| `{home}` | the user's home folder |
| `{project}` | the project name from the profile's Identity section, falling back to `{repo_name}` |

A leading `~` is allowed and means the same as `{home}`. Drive letters and backslashes never appear
in a committed file: the resolver produces them, documents and profiles do not carry them.

Examples:

- `{repo_parent}/evidence` (the default)
- `{repo_parent}/{repo_name}-evidence`
- `{home}/evidence/{project}`
- `~/work-evidence`

## Where the spec comes from

Resolution order, highest first:

1. The `--spec` argument.
2. The `EVIDENCE_ROOT` environment variable.
3. The `Evidence root:` line of `CLAUDE.local.md` at the repository root.
4. The same line of `CLAUDE.project.md` at the repository root.
5. The default, `{repo_parent}/evidence`.

The line is matched case-insensitively, with or without a leading list dash, and backticks around
the value are stripped, so all of these are the same line:

```
- Evidence root: {repo_parent}/evidence
Evidence root: `{repo_parent}/evidence`
- evidence root:   {repo_parent}/evidence
```

`CLAUDE.project.md` is the committed profile, so it holds the project's convention.
`CLAUDE.local.md` is the per-machine file, uncommitted, so it holds this machine's exception. That
is the whole migration path: a machine that already has an evidence tree keeps it by writing one
line in `CLAUDE.local.md`, and nobody else's setup changes.

## Using it from a skill or a script

```
python <kit>/scripts/evidence-path.py --id 1234 --create
```

prints one line, the absolute path in the native form of the running OS, and creates the folder.
Without `--create` it only prints. Other arguments:

- `--mockups` appends the shared `mockups/` folder instead of an item folder.
- `--spec <spec>` overrides everything, for a one-off run.
- `--repo <path>` names the repository; the default is the git top level of the current folder, and
  the current folder itself when it is not a repository.
- `--print-spec` prints which spec won and where it came from, on a line before the path.

Exit code 0 on success, 2 on a spec the grammar does not accept, with a one-line reason on stderr.
Skills call the resolver rather than composing a path themselves; that way a machine with its own
root is served without a single edit to a skill.

## Two legacy layouts

Both of the layouts people already keep are expressible in the same grammar, so nothing has to be
moved:

- One tree per user, a folder per project inside it: `{home}/evidence/{project}`. This is the shape
  to pick when several repositories should collect their evidence in one place, and it is why
  `{project}` exists at all. Set the project name in the profile's Identity section, otherwise the
  repository folder name is used.
- A sibling folder named after the repository: `{repo_parent}/{repo_name}-evidence`. This is the
  shape to pick when several repositories share a parent folder and one bare `evidence` beside them
  would be ambiguous.

Write the chosen line in `CLAUDE.local.md` and every skill follows it from the next call on.
