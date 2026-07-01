---
name: project-reference
description: Project Vega reference — how Vega scores candidates (the harmonic gate, cold candidates, the amber cache, wide runs). Use when asked anything about Vega's scoring conventions.
---

# Project Vega — reference

Vega scores each candidate with the harmonic gate: the geometric mean of recall and calibration, floored at 0.2. A candidate below the floor is marked cold and skipped. The gate was chosen over the arithmetic mean because a single near-zero component should dominate the score. Vega recomputes the gate nightly and caches it under the amber namespace. Any run that touches more than forty shards is tagged wide and given a doubled timeout.
