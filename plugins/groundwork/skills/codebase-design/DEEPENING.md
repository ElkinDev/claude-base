# Deepening

How to take a cluster of shallow modules and make one deep module out of it, safely, given what the cluster depends on. The vocabulary is the one in [SKILL.md](SKILL.md): module, interface, seam, adapter.

## Classify the dependencies first

The category a dependency falls into decides how the deepened module gets tested across its seam, so classify before you design.

**In-process.** Pure computation, in-memory state, no input or output. Always safe to deepen: merge the modules and test straight through the new interface. Nothing has to be adapted.

**Locally substitutable.** A dependency with a stand-in that runs inside the test suite, such as an embedded build of the database or an in-memory filesystem. Safe to deepen when the stand-in already exists. The stand-in runs in the suite and the seam stays internal, so no port appears on the module's external interface.

**Remote but owned.** Your own services on the far side of a network hop. Put a port at the seam. The deep module keeps the logic and the transport arrives as an adapter: an in-memory one under test, a real one in production. The shape of the recommendation is that the logic sits in one deep module even though it is deployed across a network.

**External and not yours.** A third-party service you do not control. The deepened module takes it as an injected port and the tests supply a stand-in adapter.

## Seam discipline

- One adapter is a hypothetical seam; two adapters make it a real one. Do not add a port until at least two adapters are justified, which in practice usually means production and test. A port with one adapter is indirection and nothing else.
- Internal seams are not external seams. A deep module may hold seams its own tests use, and those stay private. Do not publish one on the interface just because a test happens to reach for it.

## Testing: replace rather than layer

- Once tests exist at the deepened module's interface, the old tests on the shallow parts are waste. Delete them instead of keeping both layers.
- Write the new tests at the deepened interface. The interface is where the test surface is.
- Assert on what is observable through the interface, never on internal state.
- A test written that way survives an internal refactor, because it describes behaviour. A test that has to change whenever the implementation changes was testing past the interface.
