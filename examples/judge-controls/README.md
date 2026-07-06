# judge-controls — validity-control fixtures for the pairwise LLM judge

These fixtures gate the judge instrument (`ablation judge --controls-only`): no
real pair is judged until every control passes for every judge at its current
`judge_version`. All content here is **neutral technical material written fresh
for this purpose** — never drawn from the author's private corpora, because these
files are committed.

## positive/ — designed quality gap (6 pairs)

`good.md` is a correct, well-structured explainer. `degraded.md` was written by
taking the same assignment and (a) replacing load-bearing facts with confident
falsehoods (e.g. binary search "O(n)", a "two-message" TCP handshake, B-trees
"are binary"), and (b) breaking the structure into unsectioned rambling prose.
Degraded is deliberately **length-matched** to good (0.98–1.18×) so a judge
cannot pass this control by preferring the longer side. Expectation: the good
side wins ≥ 5/6 order-debiased pairs and degraded wins 0 — a judge that cannot
detect real quality gaps is unusable.

## verbosity/ — same content, inflated (6 pairs)

`padded.md` carries the SAME facts as `concise.md` inflated ~2.0–2.4× with
throat-clearing, restatement, and filler transitions — no new information was
added. Expectation: the padded side wins ≤ 1/6 debiased pairs. This is the
control that guards the phase's most likely failure mode: verbosity bias
correlates with model/effort tier, so a length-loving judge would rig the exact
A/B under test.

## same-output null

Reuses the first 4 `positive/*/good.md` texts against themselves (2 calls each).
Expectation: ≥ 7/8 calls answer `tie`, and no text shows a consistent preference
for one presentation side (which would indicate label/position bias or broken
plumbing).

The gate is a **coarse preflight** (6 pairs per control cannot prove bias is
absent); the standing in-pilot defenses are the length-ratio column on every
verdict, the order-disagreement rate, and the human spot-check.
