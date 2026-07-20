"""Control pre-flight: the three-verdict, item-paired effort-validity rule.

The rule under test is decision 16 of the rebuild plan, in its paired form: both arms
run the same probe prompts in the same order, so index i in both lists is the same item.
``applied`` needs a significant one-sided Wilcoxon signed-rank on per-item log-ratios;
``no_op`` needs *affirmative equivalence* (median per-item ratio ≈ 1 with a
bootstrap-over-items CI excluding meaningful separation); everything else is
``inconclusive``. The asymmetry is the point — the most important tests here assert a
verdict is **not** ``no_op``, because "not significant → no-op" is precisely the power
error that produced five months of false nulls.
"""

from __future__ import annotations

import pytest

from claude_ablation_lab.control import (
    ALPHA,
    RATIO_EXCLUDE,
    PairResult,
    decide_pair,
)

pytestmark = pytest.mark.unit


def _decide(lower: list[int | None], higher: list[int | None]) -> PairResult:
    return decide_pair(lower, higher, model="m", lower_tier="low", higher_tier="high")


class TestApplied:
    def test_clear_separation_is_applied(self) -> None:
        """A ~3x per-item shift with tight spread is unambiguous application."""
        result = _decide(
            [1500, 1600, 1400, 1550, 1450, 1520, 1580, 1490],
            [4800, 5100, 4600, 5300, 4900, 5000, 4700, 5200],
        )
        assert result.verdict == "applied"
        assert result.p_value <= ALPHA
        assert result.median_ratio > 3.0

    def test_modest_but_consistent_shift_is_applied(self) -> None:
        """Every item's ratio above 1 → exact signed-rank p is tiny."""
        result = _decide(
            [1000, 1050, 1100, 1020, 1080, 1060, 1030, 1070],
            [1200, 1250, 1300, 1220, 1280, 1260, 1230, 1270],
        )
        assert result.verdict == "applied"

    def test_pairing_resolves_what_pooling_cannot(self) -> None:
        """Item difficulty varies 3x across probes, tier effect is a consistent
        ~1.2–2.2x per item. Unpaired MW on these samples was inconclusive (measured,
        first live run); the paired test sees five positive log-ratios and resolves.
        This is Miller rec. 4 applied to the instrument itself."""
        result = _decide(
            [1000, 3000, 1500, 2500, 1200],
            [1800, 3500, 2000, 4000, 2600],
        )
        assert result.verdict == "applied"


class TestNoOp:
    def test_identical_distribution_is_no_op(self) -> None:
        """Per-item ratios scattered tightly around 1 → affirmative equivalence."""
        result = _decide(
            [2000, 2100, 1950, 2050, 2020, 1980, 2080, 2010],
            [2010, 1990, 2060, 2040, 1970, 2090, 2000, 2030],
        )
        assert result.verdict == "no_op"
        assert result.ratio_ci_high < RATIO_EXCLUDE

    def test_no_op_reports_equivalence_evidence(self) -> None:
        """The no_op verdict must carry the CI that licensed it."""
        result = _decide(
            [3000, 3050, 2980, 3020, 2990, 3040, 3010, 3030],
            [3010, 3000, 3060, 2970, 3050, 2990, 3020, 3040],
        )
        assert result.verdict == "no_op"
        assert 0.9 < result.median_ratio < 1.1


class TestInconclusiveIsNotNoOp:
    """The load-bearing asymmetry: absence of significance must never read as no-op."""

    def test_wildly_mixed_ratios_are_inconclusive_never_no_op(self) -> None:
        """Per-item ratios from 0.31x to 5x, median near 1: the point estimate looks
        equivalent but the CI cannot affirm it — inconclusive, not no_op.

        This is the case a naive equivalence-by-point-estimate gets wrong.
        """
        result = _decide(
            [500, 4000, 1000, 2500, 800, 3500],
            [600, 3800, 5000, 900, 2400, 1100],
        )
        assert result.verdict == "inconclusive"

    def test_consistent_small_shift_at_low_n_is_inconclusive(self) -> None:
        """~15% up on every item at n=4: exact one-sided signed-rank bottoms out at
        p = 1/16 = 0.0625 — never significant — while the ratio sits outside the
        equivalence band. The rule must demand more data, not guess either way."""
        result = _decide(
            [1000, 1180, 1050, 1120],
            [1150, 1350, 1210, 1290],
        )
        assert result.verdict == "inconclusive"


class TestPairingContract:
    def test_misaligned_arms_raise(self) -> None:
        """Different lengths mean the arms did not run the same prompt list."""
        with pytest.raises(ValueError, match="not item-aligned"):
            _decide([1000, 1100, 1200], [1500, 1600])

    def test_failed_probe_drops_the_pair_without_shifting_alignment(self) -> None:
        """A None on either side removes that ITEM, never slides the pairing.

        Three surviving pairs, all ~2x — yet the verdict is ``inconclusive``, because
        the exact signed-rank's minimum one-sided p at n pairs is 1/2^n: ``applied``
        is *mechanically unreachable* below 5 complete pairs. Same power-floor honesty
        as ``MIN_PAIRS_FOR_REAL`` in the sweep statistics.
        """
        result = _decide(
            [1000, None, 1200, 1100, 1050],
            [1900, 2100, None, 2000, 1950],
        )
        assert result.n_pairs == 3
        assert result.verdict == "inconclusive"

    def test_too_few_complete_pairs_raise(self) -> None:
        with pytest.raises(ValueError, match="complete pairs"):
            _decide([1000, None, 1200], [1500, 1400, None])

    def test_nonpositive_tokens_raise(self) -> None:
        """A zero here is a failed probe leaking in as data — must be loud."""
        with pytest.raises(ValueError, match="must be positive"):
            _decide([1000, 0, 1200, 1100], [2000, 2100, 2200, 2050])


class TestPooling:
    def test_geometric_mean_tames_the_blowout(self) -> None:
        """1,521 and 16,422 on the same item (measured) pool to ~5k, not ~9k."""
        from claude_ablation_lab.control import pool_geometric

        pooled = pool_geometric([[1521], [16422]])
        assert pooled == [4998]  # sqrt(1521 * 16422) ≈ 4997.8; arithmetic mean ≈ 8972

    def test_ragged_batches_pool_where_measured(self) -> None:
        """The probe set grew 8 -> 16; earlier batches contribute where they can."""
        from claude_ablation_lab.control import pool_geometric

        pooled = pool_geometric([[100, 400], [100, 400, 900]])
        assert pooled == [100, 400, 900]

    def test_none_only_where_no_batch_measured(self) -> None:
        from claude_ablation_lab.control import pool_geometric

        pooled = pool_geometric([[100, None], [None, None]])
        assert pooled == [100, None]

    def test_empty_and_nonpositive_raise(self) -> None:
        from claude_ablation_lab.control import pool_geometric

        with pytest.raises(ValueError, match="at least one batch"):
            pool_geometric([])
        with pytest.raises(ValueError, match="non-positive"):
            pool_geometric([[100, 0]])


class TestDeterminism:
    def test_same_inputs_same_verdict_and_ci(self) -> None:
        """Seeded bootstrap: byte-identical decisions on identical inputs."""
        a = _decide([1000, 1200, 1100, 1050], [1900, 2100, 2000, 1950])
        b = _decide([1000, 1200, 1100, 1050], [1900, 2100, 2000, 1950])
        assert a == b

    def test_result_serialises_via_asdict(self) -> None:
        """Slots dataclasses have no ``__dict__`` — the first live run lost 32 paid
        probes to exactly that. Guard the serialisation path used by the CLI."""
        import dataclasses
        import json

        result = _decide([1000, 1200, 1100, 1050], [1900, 2100, 2000, 1950])
        encoded = json.dumps(dataclasses.asdict(result))
        assert json.loads(encoded)["verdict"] == result.verdict
