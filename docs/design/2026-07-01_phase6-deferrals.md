# Phase 6 — deferred items (decision record)

*2026-07-01. Phase D shipped plotting (D1), the anchor-strict grader (D2), and the public
demo-infra A/B (D6). Three Phase-6 items were considered and deliberately **not** built; this
records why, so each omission reads as a decision, not an oversight.*

## D3 — ApiRunner (deferred)

An `anthropic`-SDK runner conforming to the `Runner` protocol is straightforward — effort maps
1:1 to the API's `output_config.effort`, and the errors map cleanly onto the
`ok|rate_limited|infra_error|timeout|parse_fail` taxonomy. It is **not built** because it is
structurally infra-blind: it cannot exercise the `variant = infra_repo@ref` axis — the entire
reason this harness exists — and cannot run agentic tasks (T2) at all. Its only honest use is an
infra-stripped base-model baseline on T1/T3, at the cost of a real-\$ vs subscription cost split
(a new `runner_kind` ledger field + an `analyze` grouping change) and an adapter pinned to a
moving API. The `api` optional-dependency in `pyproject.toml` remains the declared seam; build
only if a concrete base-model-baseline need appears.

## D4 — probability-elicitation AUROC (deferred)

The T1 classification grader reports hard-label AUROC (== balanced accuracy), which already
answers the config-delta question. True ranking AUROC needs a per-example probability, which
changes the T1 JSON schema → changes `spec_sha` → forces a **full paid T1 re-sweep** (not a free
regrade), to measure notoriously-miscalibrated verbalized confidences. Build only for a dedicated
calibration study (Brier / ECE), and then as a *parallel* task (`t1-clf-prob-v1`), never a
replacement.

## D5 — "book spinoff" (parked)

Authoring, not code. The harness's methodology (the leakage gate, infra-sensitive variants,
run/grade decoupling, cheapest-per-successful-outcome) and the Phase-C figures could become
chapter material, but that is out of scope for this repository. Needs scoping — a chapter draft?
a reproducible appendix? just the figures? — before any work.
