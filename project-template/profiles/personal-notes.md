# Project profile: personal notes / scratch (example)

For a personal, non-work folder: notes, documents, everyday topics. No tracker, light discipline, and
you may keep the working language your own. This is the profile behind a personal scope: a
notes or scratch folder kept outside your work repositories.

## Identity
- Project name: personal
- Repo(s): a local folder (may not be a git repo at all)
- Integration branch: n/a
- Integration branch is PR-only: n/a

## Tracker
- Tracker: none
- Location: n/a
- Item id format: n/a
- Commit/PR link convention: none
- CLI: none

## Stack and quality gates
- No build. Documents, notes, and scratch files.

## Git discipline
- Hand-off only: no. Commit freely if the folder is a repo, or do not use git at all.
- Branch naming: n/a

## Language override
- This scope overrides the global "artifacts in English" default: chat and personal notes may be in
  your own language (for example Spanish). Everything else in the global rules still applies (no
  em-dashes, human voice, no AI attribution, no self-validating assertions).

## Documents (markitdown)
- Wire the markitdown hook (`claude/hooks/markitdown-read.py`) as a PreToolUse(Read) hook so PDFs and
  Office files are converted to Markdown before reading. It caches the conversion and steps aside for
  scanned files with no text layer.

## Testing and e2e
- n/a

## Evidence
- Not required. Keep a note only when a result is worth replaying.

## Environments
- n/a
