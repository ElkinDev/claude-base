---
name: codebase-design
description: Vocabulary for deep modules: interface, seam, adapter, depth, leverage, locality. Use when shaping an interface, placing a seam, or making code testable.
---

# Codebase design

Design deep modules: a lot of behaviour behind a small interface, sitting at a clean seam and tested through that interface. Use these terms wherever code is being shaped or restructured. What depth buys is leverage for the caller, locality for whoever maintains the code, and a test surface both of them share.

## Glossary

Use the terms as written. Do not reach for "component", "service", "API" or "boundary" instead, because one vocabulary is the whole point of having one.

**Module**: anything that has an interface and an implementation. Scale-agnostic on purpose, so a function, a class, a package and a slice that spans tiers are all modules.

**Interface**: everything a caller has to know to use the module correctly. The type signature, and with it the invariants, the ordering constraints, the error modes, the configuration it requires and how it performs. Wider than a signature, which covers the type surface alone.

**Implementation**: the code inside the module. Say adapter instead when the seam is what you are discussing, because a small adapter can carry a large implementation and a large adapter a tiny one.

**Depth**: how much behaviour a caller or a test can drive per unit of interface it has to learn. A module is deep when a lot sits behind a small interface, and shallow when the interface costs about as much to learn as the implementation cost to write.

**Seam**: a place where behaviour can be changed without editing that place, and so the location where a module's interface lives. Where to put it is a decision of its own, separate from what goes behind it. Prefer it to "boundary", which already means a bounded context elsewhere.

**Adapter**: something concrete that satisfies an interface at a seam. It names a role, not a substance.

**Leverage**: what depth gives the caller. One implementation repays itself across every call site and every test that crosses the interface.

**Locality**: what depth gives the maintainer. Change, defects, knowledge and verification collect in one place instead of spreading over the callers.

## Deep against shallow

A deep module is a small interface over a large implementation: few entry points, simple parameters, the difficulty kept inside. A shallow one is a wide interface over a thin implementation that mostly forwards. When you draft an interface, ask what it would take to drop an entry point, to simplify a parameter, and to move one more decision inside.

## Principles

- Depth belongs to the interface, not to the implementation. Behind it a module may be built from small swappable parts; they are simply not on the interface. A module can carry internal seams, private to its implementation and used by its own tests, as well as the external seam at its interface.
- The deletion test. Picture the module gone. If the complexity goes with it, the module was forwarding. If the complexity reappears in every caller, it was earning its place.
- The interface is also the test surface. Tests and callers cross the same seam, so wanting to test past the interface is a sign the module has the wrong shape.
- One adapter is a hypothetical seam; two adapters make it a real one. Do not open a seam until something genuinely varies across it.

## Designing for testability

- Take dependencies as arguments instead of constructing them inside, so a test can hand over its own.
- Return a result instead of mutating something the test then has to go and inspect.
- Keep the surface small. Fewer entry points is fewer tests, and fewer parameters is less setup for each of them.

## How the terms relate

A module has exactly one interface, the surface it shows to callers and tests. Depth is measured against that interface. A seam is where the interface lives, an adapter sits at the seam and satisfies the interface, and depth is what produces leverage and locality.

## Framings this vocabulary rejects

- Depth as a ratio of implementation lines to interface lines. It rewards padding the body. Depth here is leverage.
- Interface as the language keyword, or as the list of public methods. Too narrow: the interface is every fact a caller has to know.
- Boundary as a synonym for seam. That word is already taken by the bounded context of domain-driven design. Say seam, or say interface.

## Going further

- [DEEPENING.md](DEEPENING.md): how to deepen a cluster of shallow modules given what they depend on, and how the dependency category decides the way the result is tested.
- [DESIGN-IT-TWICE.md](DESIGN-IT-TWICE.md): design the interface several incompatible ways at once, then compare them on depth, locality and seam placement.
