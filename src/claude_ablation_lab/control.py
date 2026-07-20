"""Effort-validity pre-flight: prove the knob moved before plotting anything against it.

The original harness's fatal defect was trusting a flag's *acceptance* as evidence of its
*application* — ``--effort`` was accepted for Haiku 4.5 and silently discarded, so five
effort labels named one configuration. This module establishes application behaviourally:
for each adjacent tier pair, do the realized token distributions actually separate?

Decision rule (decision 16) — three verdicts, asymmetric by design, **paired by item**
--------------------------------------------------------------------------------------

Both tiers run the *same probe prompts in the same order*, so the analysis is paired —
per-item token ratios, not pooled distributions. The first live run used an unpaired
Mann-Whitney and item-difficulty variance (one item can 10× another) swamped the tier
contrast on both arms; pairing removes it entirely (Miller arXiv:2411.00640, rec. 4 —
the same principle the sweep statistics use, applied to the instrument itself).

A naive "not significant → no-op" would commit the exact power error this rebuild is
about (reading "not detected" as "absent") and silently delete real treatments at n≈10:

- ``applied`` — a one-sided exact Wilcoxon signed-rank on per-item log-ratios shows the
  higher tier spends more tokens on the same items (p ≤ ALPHA).
- ``no_op`` — affirmative equivalence only: the median per-item ratio is ≈1 **and** a
  bootstrap-over-items CI on that median excludes meaningful separation (upper bound
  < RATIO_EXCLUDE).
- ``inconclusive`` — anything else. Callers must treat this as "block and sample more",
  never as either of the other verdicts.

The asymmetry is intentional: falsely collapsing a real tier destroys a treatment, while
falsely keeping a no-op merely runs redundant cells whose paired diffs self-reveal as 0.

Probe-item requirement (measured, 2026-07-20)
---------------------------------------------

Adaptive thinking spends ~nothing on trivial items at *every* ceiling — a trivial probe
produced byte-identical 54-token outputs from sonnet-5 at ``low`` and ``high``. Effort is
a ceiling, not a dial (Hu & Wang, arXiv:2605.16938), so **probes must be hard enough
that the ceiling binds** or every real tier looks like a no-op. The bundled probes are
multi-step reasoning items. Control measures *tokens*, not correctness, so they need no
gold answers.

Deliberate escape hatch
-----------------------

:class:`~claude_ablation_lab.provider.cli.CliProvider` refuses an effort tier the Models
API says is unsupported — that is the production guarantee. Control is the **one** place
allowed to send an un-validated tier, because detecting silent clamping requires sending
the flag the provider would refuse. :func:`probe_cli_tokens` therefore builds its own
argv rather than going through the provider, and says so loudly in its output.
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
import subprocess
from dataclasses import dataclass
from typing import Literal

import numpy as np

from claude_ablation_lab.runner import AUTH_ENV_STRIP, extract_json

__all__ = [
    "ALPHA",
    "RATIO_EQUIV",
    "RATIO_EXCLUDE",
    "PROBE_PROMPTS",
    "Verdict",
    "PairResult",
    "decide_pair",
    "pool_geometric",
    "probe_cli_tokens",
]

logger = logging.getLogger(__name__)

#: One-sided significance level for the ``applied`` verdict.
ALPHA = 0.05
#: ``no_op`` requires the observed median ratio (higher tier / lower tier) within this
#: factor of 1 — i.e. the tiers look like the same configuration.
RATIO_EQUIV = 1.10
#: ...and the bootstrap CI upper bound on that ratio below this — i.e. the data
#: affirmatively *excludes* meaningful separation, rather than merely failing to show it.
RATIO_EXCLUDE = 1.25

_BOOTSTRAP_RESAMPLES = 4000
_BOOTSTRAP_SEED = 42

Verdict = Literal["applied", "no_op", "inconclusive"]

#: Multi-step reasoning probes — deliberately effortful so the effort ceiling binds
#: (see module docstring). Token elicitors, not graded items: no gold answers exist or
#: are needed.
PROBE_PROMPTS: tuple[str, ...] = (
    "A tank holds 2400 L. Pipe A fills it in 6 h, pipe B in 8 h, and drain C empties "
    "it in 12 h. All three are opened at 09:00, but B is closed after 90 minutes. At "
    "what clock time is the tank full? Work through it step by step.",
    "Find all integer solutions (x, y) with 0 < x < y of 1/x + 1/y = 1/12, and prove "
    "your list is complete.",
    "A 5-digit number is divisible by 9; deleting its middle digit yields a 4-digit "
    "number divisible by 9. What are the possible middle digits, and why?",
    "Three friends split a restaurant bill. Ana pays 40% of it, Ben pays 25 less than "
    "half the bill, and Cara pays the remaining 19. Reconstruct the bill and each "
    "share, checking consistency.",
    "How many trailing zeros does 130! have in base 12? Explain the prime-power "
    "counting carefully.",
    "A knight starts at a1 on an empty chessboard. What is the minimum number of moves "
    "to reach h8, and how many distinct minimum-length routes are there? Reason it out.",
    "Solve for x: log_2(x) + log_4(x) + log_16(x) = 7. Give an exact simplified answer "
    "and verify it.",
    "An urn has 5 red and 7 blue balls. Balls are drawn without replacement until the "
    "first red appears. What is the expected number of draws? Derive it, don't guess.",
    # Probes 9-16, added when n=8 left both live verdicts inconclusive: per-item
    # run-to-run variance on hard items is large (measured 0.39x-2.71x on identical
    # prompts), so the signed-rank needs more pairs to resolve either direction.
    "A clock's hour and minute hands overlap at 12:00. At what exact time do they "
    "next overlap? Give the answer to the nearest second, with the derivation.",
    "Find the smallest positive integer n such that n/2 is a perfect square, n/3 is "
    "a perfect cube, and n/5 is a perfect fifth power. Show the exponent bookkeeping.",
    "Two trains 90 km apart head toward each other at 40 and 50 km/h. A bird flies "
    "between them at 75 km/h until they meet, reversing instantly each time it "
    "reaches a train. How far does the bird fly, and what distance does each train "
    "cover? Also give the bird's first-leg distance.",
    "In how many ways can 8 identical rooks be placed on an 8x8 board so no two "
    "attack each other and none stands on the main diagonal? Explain via derangements.",
    "Evaluate the sum 1/(1*2*3) + 1/(2*3*4) + ... + 1/(18*19*20) exactly as a "
    "fraction in lowest terms, using partial fractions.",
    "A password has exactly 6 characters over {A,B,C,1,2,3}, must contain at least "
    "one letter and at least one digit, and no character may appear three or more "
    "times. How many passwords are there? Reason with inclusion-exclusion.",
    "Water flows into a conical tank (apex down, half-angle 30 degrees) at 2 L/min. "
    "How fast is the water level rising when the depth is 20 cm? Work in consistent "
    "units and give cm/min.",
    "Let f(x) = x^3 - 3x + 1. How many real roots does f have, in which intervals "
    "do they lie, and what is the sum of their squares? Justify each step.",
)


@dataclass(frozen=True, slots=True)
class PairResult:
    """The verdict for one adjacent tier pair on one model.

    Parameters
    ----------
    model, lower_tier, higher_tier:
        The contrast probed. ``lower_tier`` may be ``"default"`` when the flag was
        omitted entirely (the baseline arm of a clamp probe).
    verdict:
        See module docstring. ``inconclusive`` blocks; it is never a coin flip.
    p_value:
        One-sided exact Wilcoxon signed-rank p on per-item log-ratios, for "the higher
        tier spends more tokens on the same items".
    median_ratio:
        Median of the per-item ratios (higher / lower on the same prompt).
    ratio_ci_high:
        Upper bound of the bootstrap-over-items CI on that median ratio — the
        equivalence evidence. ``no_op`` requires it below :data:`RATIO_EXCLUDE`.
    n_pairs:
        Item pairs where *both* arms produced an ``ok`` probe; a failure in either arm
        drops the pair, never shifts the alignment.
    """

    model: str
    lower_tier: str
    higher_tier: str
    verdict: Verdict
    p_value: float
    median_ratio: float
    ratio_ci_high: float
    n_pairs: int


def decide_pair(
    lower_tokens: list[int | None],
    higher_tokens: list[int | None],
    *,
    model: str,
    lower_tier: str,
    higher_tier: str,
) -> PairResult:
    """Classify one adjacent tier pair from **item-aligned** token samples.

    Both lists must come from the same probe prompts in the same order — index ``i``
    in both lists is the same item, and the analysis is paired on it. ``None`` marks a
    failed probe; a pair with a failure on either side is dropped (alignment is never
    shifted, which would silently pair different items).

    Pure and deterministic (seeded bootstrap) — the unit-testable core; live probing
    lives in :func:`probe_cli_tokens`.

    Raises
    ------
    ValueError
        If the lists differ in length (alignment broken upstream), fewer than 3
        complete pairs survive, or a non-``None`` count is non-positive.

    Notes
    -----
    The exact one-sided signed-rank's minimum p at ``n`` pairs is ``1/2**n``, so
    ``applied`` is mechanically unreachable below **5** complete pairs — the same
    power-floor honesty as ``MIN_PAIRS_FOR_REAL`` in the sweep statistics. At 3–4
    pairs only ``no_op`` or ``inconclusive`` can result.
    """
    if len(lower_tokens) != len(higher_tokens):
        raise ValueError(
            f"arms are not item-aligned: {len(lower_tokens)} vs {len(higher_tokens)} "
            "probes; both tiers must run the same prompt list"
        )
    pairs = [
        (lo, hi)
        for lo, hi in zip(lower_tokens, higher_tokens, strict=True)
        if lo is not None and hi is not None
    ]
    if len(pairs) < 3:
        raise ValueError(
            f"need >=3 complete pairs to classify, got {len(pairs)}; "
            "collect more probes instead of classifying on noise"
        )
    if any(lo <= 0 or hi <= 0 for lo, hi in pairs):
        raise ValueError("token counts must be positive; a 0 here is a failed probe leaking in")

    import math

    from scipy.stats import wilcoxon  # type: ignore[import-untyped]

    log_ratios = [math.log(hi / lo) for lo, hi in pairs]
    ratios = [hi / lo for lo, hi in pairs]

    # One-sided exact signed-rank: does the higher tier spend more on the same items?
    # zero_method="zsplit" keeps exact ties (identical outputs) informative rather
    # than discarding them — a clamped model produces many near-zero log-ratios and
    # dropping them would shrink n exactly where the no_op evidence lives.
    wr = wilcoxon(log_ratios, alternative="greater", zero_method="zsplit")
    p_value = float(wr.pvalue)

    median_ratio = statistics.median(ratios)
    ratio_ci_high = _bootstrap_median_ci_high(ratios)

    verdict: Verdict
    if p_value <= ALPHA:
        verdict = "applied"
    elif (1 / RATIO_EQUIV) <= median_ratio <= RATIO_EQUIV and ratio_ci_high < RATIO_EXCLUDE:
        verdict = "no_op"
    else:
        verdict = "inconclusive"

    return PairResult(
        model=model,
        lower_tier=lower_tier,
        higher_tier=higher_tier,
        verdict=verdict,
        p_value=p_value,
        median_ratio=median_ratio,
        ratio_ci_high=ratio_ci_high,
        n_pairs=len(pairs),
    )


def pool_geometric(batches: list[list[int | None]]) -> list[int | None]:
    """Pool replicate probe batches into one stabilized draw per item.

    Per-item **geometric** mean across batches — token counts are ratio-scaled and
    heavy-tailed (measured: the same item drew 1,521 and 16,422 tokens on identical
    configs), so the arithmetic mean would let one blowout dominate the pooled value.
    Batches may have different lengths (the probe set grew from 8 to 16); shorter
    batches simply contribute nothing beyond their length. An item pools over
    whichever batches measured it; ``None`` only where no batch did.

    Raises
    ------
    ValueError
        If *batches* is empty or any present count is non-positive.
    """
    if not batches:
        raise ValueError("need at least one batch to pool")
    length = max(len(batch) for batch in batches)
    pooled: list[int | None] = []
    for index in range(length):
        draws: list[int] = [
            draw for batch in batches if index < len(batch) and (draw := batch[index]) is not None
        ]
        if any(draw <= 0 for draw in draws):
            raise ValueError(f"non-positive token count at item {index}; failed probe leaked in")
        if not draws:
            pooled.append(None)
            continue
        log_mean = sum(math.log(draw) for draw in draws) / len(draws)
        pooled.append(round(math.exp(log_mean)))
    return pooled


def _bootstrap_median_ci_high(ratios: list[float]) -> float:
    """Upper bound of the 95% percentile bootstrap CI on the median per-item ratio.

    Resamples *items* (the pairing unit), matching the paired design.
    """
    rng = np.random.default_rng(_BOOTSTRAP_SEED)
    values = np.asarray(ratios, dtype=float)
    medians = np.empty(_BOOTSTRAP_RESAMPLES)
    for i in range(_BOOTSTRAP_RESAMPLES):
        medians[i] = np.median(rng.choice(values, values.size, replace=True))
    return float(np.quantile(medians, 0.975))


def probe_cli_tokens(
    model: str,
    tier: str | None,
    *,
    prompts: tuple[str, ...] = PROBE_PROMPTS,
    timeout_s: float = 600.0,
    claude_bin: str = "claude",
) -> list[int | None]:
    """Collect realized output-token counts for *model* at *tier* via ``claude -p``.

    **Deliberately un-validated** (see module docstring): this sends ``--effort`` even
    where the Models API says the model has no effort parameter, because detecting a
    silent clamp requires sending the flag production code refuses. ``tier=None`` omits
    the flag entirely — the default-config baseline.

    Returns a list **aligned to** *prompts*: index ``i`` is prompt ``i``'s output-token
    count, or ``None`` for a failed probe. Alignment is the contract that makes the
    paired analysis valid — a dropped-and-compacted list would silently pair different
    items across arms.
    """
    env = dict(os.environ)
    for key in AUTH_ENV_STRIP:
        env.pop(key, None)

    tokens: list[int | None] = []
    for i, prompt in enumerate(prompts):
        argv = [claude_bin, "-p", prompt, "--model", model]
        if tier is not None:
            argv += ["--effort", tier]
        argv += [
            "--tools",
            "",
            "--output-format",
            "json",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--exclude-dynamic-system-prompt-sections",
        ]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed binary, no shell
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
                stdin=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("control probe %d %s/%s failed: %s", i, model, tier, exc)
            tokens.append(None)
            continue
        payload = extract_json(proc.stdout)
        if payload is None or payload.get("is_error"):
            logger.warning(
                "control probe %d %s/%s unusable: rc=%s err=%r",
                i,
                model,
                tier,
                proc.returncode,
                str(payload.get("result"))[:80] if payload else proc.stdout[:80],
            )
            tokens.append(None)
            continue
        usage = payload.get("usage")
        out = usage.get("output_tokens") if isinstance(usage, dict) else None
        if isinstance(out, int) and out > 0:
            tokens.append(out)
        else:
            logger.warning("control probe %d %s/%s: no output_tokens in payload", i, model, tier)
            tokens.append(None)
    return tokens


def _main() -> None:  # pragma: no cover - thin live harness, exercised manually
    """Minimal live entry point: ``python -m claude_ablation_lab.control MODEL T1 T2``.

    Token samples are printed *before* the verdict is computed: 16 paid probes must
    never be lost to a failure in the decision or serialisation step (the first run of
    this harness did exactly that — ``slots=True`` dataclasses have no ``__dict__``).
    """
    import dataclasses
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    model, lower, higher = sys.argv[1], sys.argv[2], sys.argv[3]
    lo = probe_cli_tokens(model, None if lower == "default" else lower)
    print(json.dumps({"model": model, "tier": lower, "tokens": lo}), flush=True)
    hi = probe_cli_tokens(model, None if higher == "default" else higher)
    print(json.dumps({"model": model, "tier": higher, "tokens": hi}), flush=True)
    result = decide_pair(lo, hi, model=model, lower_tier=lower, higher_tier=higher)
    print(json.dumps(dataclasses.asdict(result), indent=2))


if __name__ == "__main__":  # pragma: no cover
    _main()
