---
name: bulk-read
description: Send a whole-file read to a cheap reader subagent and keep only its bullets. Use for orientation over large files a reviewer or an analyst will not edit.
---

# Bulk Read

Work agents run on the strong model. The bulk reader is the one exception: it reads whole files on
a small model and returns bullets, so the file itself never lands in your window. What you buy is
context hygiene, not money.

## When to delegate

- One orientation question over a file above about 350 lines: what does this do, what does it
  expose, where is X handled.
- Several files at once when the question spans them and you only need to know which of them to
  open for real.
- Only when you will not edit the files. Reading to understand is the case; reading to change is
  not.

## How

Call the Agent tool with `subagent_type: bulk-reader`. Give it exactly two things: the one question,
and the absolute paths of the files. No other instructions, no method, no format notes; the agent
definition already carries them, and anything you add is paid for on a model that will not use it.

Up to four files per call; more files, more calls. The reader has a small turn budget, and a long
list spends it on reading instead of answering. Splitting also lets the calls run in parallel.

## What comes back

Structured bullets, each naming a file and a symbol, closed by the reader's orientation line. About
400 tokens, which is what stays in your context after the call.

## The two limits

Never for a file the caller will edit: an edit needs line numbers the summary does not carry.

Never a verdict on a summary alone: any claim that reaches a finding, a verdict or an edit is verified with a direct ranged read.

## Cost

About 34 K Haiku tokens and 37 s per call measured on an 841-line file; the caller keeps about 400 tokens.

## When not to use it

- Small files. Read them yourself; the call costs more than the read.
- Debugging, where the answer is in the exact lines and the exact order.
- Safety-critical code: money, deletion, sync, auth. Read those yourself.
- Anything you are about to change.
