# Design it twice

Explore several interfaces for the same deepening candidate before committing to one, because a first idea is rarely the best one available. Run the exploration in parallel subagents. The vocabulary is the one in [SKILL.md](SKILL.md): module, interface, seam, adapter, leverage.

## 1. Frame the problem space

Before any subagent starts, write the problem space out for a reader: the constraints a new interface has to satisfy, the dependencies it would rest on and which category each falls into (see [DEEPENING.md](DEEPENING.md)), and a rough code sketch that exists only to make the constraints concrete. It is not a proposal, so do not let it read as one.

Publish that framing, then move straight on. It is read while the subagents are working, not before they start.

## 2. Send out the designs

Spawn three or more subagents at once, each asked for an interface that differs from the others in kind and not in detail. Each gets a technical brief of its own: the files in play, the coupling that exists today, the dependency category from [DEEPENING.md](DEEPENING.md), and what is meant to end up behind the seam. That brief is separate from the reader-facing framing above.

Give each one a different constraint to design under. Useful ones:

- Smallest possible interface, three entry points at the most, as much leverage per entry point as can be had.
- Most flexible: many uses supported, extension expected.
- Optimised for the commonest caller, so the default case takes no arguments worth mentioning.
- Built around ports and adapters, when the dependencies cross a seam.

Hand every brief both the vocabulary of [SKILL.md](SKILL.md) and the project's own domain terms, which in a kit project live under `docs/00-product/`, so the designs name things the same way as each other and as the codebase.

Ask each subagent for five things: the interface, including invariants, ordering and error modes; a usage example from a caller's side; what the implementation keeps hidden behind the seam; the dependency strategy and the adapters it implies; and the tradeoffs, meaning where the leverage is real and where it is thin.

## 3. Compare and recommend

Present the designs one after another so each can be taken in on its own, then compare them in prose on depth, on locality, and on where each puts the seam.

Close with your own recommendation and the reasoning behind it. If parts of two designs combine well, propose the combination. A menu is not an answer: say which one you would build.
