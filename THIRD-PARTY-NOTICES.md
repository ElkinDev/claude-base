# Third-party notices

Some skills in this repository are derived from a third-party set published under the MIT licence. The text below is that licence, reproduced in full as it requires. It covers the derived files listed here and nothing else. The design skills listed under "Skills carried whole" come from their own upstream repositories, named there with their authors, on the terms those repositories publish. Everything else in this repository is the kit's own.

The files were taken at upstream commit `3cca18b` and rewritten for this kit: the wording, the structure and the pointers are the kit's, and what carried over is the method and the vocabulary. A later refresh is therefore a comparison against that commit, not a re-copy.

## Derived files

Through the installer, under `claude/skills/`:

| File here | Taken from, at `3cca18b` |
|---|---|
| `claude/skills/decision-rounds/SKILL.md` | `skills/productivity/grilling/SKILL.md` |

Through the plugin channel, under `plugins/groundwork/`:

| File here | Taken from, at `3cca18b` |
|---|---|
| `plugins/groundwork/skills/codebase-design/SKILL.md` | `skills/engineering/codebase-design/SKILL.md` |
| `plugins/groundwork/skills/codebase-design/DEEPENING.md` | `skills/engineering/codebase-design/DEEPENING.md` |
| `plugins/groundwork/skills/codebase-design/DESIGN-IT-TWICE.md` | `skills/engineering/codebase-design/DESIGN-IT-TWICE.md` |
| `plugins/groundwork/skills/slice-plan/SKILL.md` | `skills/engineering/to-tickets/SKILL.md` |

The same notice travels with the plugin on its own channel, at `plugins/groundwork/NOTICE.md`, because a machine that installs the plugin never sees this file.

## Skills carried whole

Nine design skills reach a machine through the installer, under `claude/skills/`, as their folders were in use when they were added (2026-11-18): not rewritten for this kit, each folder with its references and scripts. Some of their one-line descriptions were shortened before that, so a later refresh is a comparison against upstream, not a blind re-copy.

| Folder here | Source repository | Author | Notes |
|---|---|---|---|
| `claude/skills/impeccable/` | https://github.com/pbakaus/impeccable | Paul Bakaus | skill 4.6.0, engine 0.1.15. The engine binary (`scripts/bin/`) is not carried: the launcher fetches the binary for its version on first run. Four rows of `scripts/data/font-index.json` were removed by the kit's sanitize guard. |
| `claude/skills/taste-skill/` | https://github.com/Leonxlnx/taste-skill | Leonxlnx | `design-taste-frontend`, the SKILL.md alone |
| `claude/skills/soft-skill/` | https://github.com/Leonxlnx/taste-skill-classics | Leonxlnx | `high-end-visual-design`, first published in taste-skill and moved there, as the taste-skill README says |
| `claude/skills/redesign-skill/` | https://github.com/Leonxlnx/taste-skill | Leonxlnx | `redesign-existing-projects`, which upstream now folds into `design-taste-frontend`, as its README says |
| `claude/skills/brandkit/` | https://github.com/Leonxlnx/imagegen-skills | Leonxlnx | first published in taste-skill and moved there, as the taste-skill README says |
| `claude/skills/emil-design-eng/` | https://github.com/emilkowalski/skills | Emil Kowalski | |
| `claude/skills/animation-vocabulary/` | https://github.com/emilkowalski/skills | Emil Kowalski | |
| `claude/skills/review-animations/` | https://github.com/emilkowalski/skills | Emil Kowalski | with its `STANDARDS.md` |
| `claude/skills/apple-design/` | https://github.com/emilkowalski/skills | Emil Kowalski | |

All nine carry `disable-model-invocation: true` except apple-design, so no session loads them on its own: a session or an agent reads the SKILL.md it needs, as the designer agent does.

## The licence

MIT License

Copyright (c) 2026 Matt Pocock

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
