## Unit vs integration tests — and the pyramid

**Unit tests** exercise one component in isolation, with collaborators replaced by fakes or stubs: fast (milliseconds), deterministic, and precise — a failure names the broken unit. **Integration tests** exercise real components together (service + database, two modules across a seam): slower and flakier, but they catch what unit tests structurally cannot — wiring errors, schema drift, serialization mismatches, transaction semantics.

The **testing pyramid** heuristic: many unit tests at the base, fewer integration tests in the middle, a handful of end-to-end tests at the top. The shape follows from economics — cost and flakiness rise with scope, so you buy most of your coverage where it is cheapest and reserve expensive tests for what only they can verify.

The pyramid is a heuristic, not a law: systems whose risk lives in composition (integration-heavy services, pipelines) legitimately shift weight toward the middle. The invariant worth keeping is the *reason* for the shape: put each behavior's test at the cheapest level that can actually falsify it.
