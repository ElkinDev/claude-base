---
name: decision-rounds
description: Put the open taste and strategy calls to the owner as one numbered block, each with a recommended answer, and settle everything else yourself with a logged reason.
---

# Decision rounds

A design is a tree of decisions. Its frontier holds those whose prerequisites are all settled, so they can be put without guessing. Work it in rounds, and send the owner only the calls that are theirs.

## Settle it yourself

Three classes never become a question. Each is a decision you record with its reason.

1. A prior ruling settles it: `CLAUDE.md`, `CLAUDE.project.md`, the memory index, an ADR under `docs/02-architecture`, a spec under `docs/03-features`.
2. A fact in the repository settles it. Establishing facts is your work, never the owner's: read the tree or send a subagent, and never ask for what you can look up. Do not block on it; only the questions downstream wait.
3. It is reversible at low cost. Take the reversible default, say which, and carry on.

## What reaches the owner

Product taste and strategy, nothing else.

## The block

One message per round, questions numbered Q1 to Qn. Each carries a one-line title, a body of two or three sentences, and its candidate answers with exactly one marked as recommended. Write the block so that "all recommended" answers the whole of it, completely and without ambiguity.

## Rounds

When the answers land, derive what is settled now and push the frontier out. Send a second block only if a taste or strategy call is still open; otherwise carry on. Stop for the block you sent, and for nothing else.
