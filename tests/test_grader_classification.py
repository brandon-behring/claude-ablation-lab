"""T1 classification grader — AUROC/F1 vs gold, bootstrap CI, leakage gate.

Skips entirely when ``eval_toolkit`` is not installed (it is not on PyPI; see
``make install``).
"""

from __future__ import annotations

import json
import math

import pytest

pytest.importorskip("eval_toolkit")

from claude_ablation_lab.graders.classification import (  # noqa: E402  (after importorskip)
    MIN_BOOTSTRAP_N,
    ClassificationGrader,
)


def _gold(n: int) -> dict[int, int]:
    """First half injection (1), second half safe (0) — balanced."""
    return {i: (1 if i < n // 2 else 0) for i in range(n)}


def _output(gold: dict[int, int], frac_correct: float) -> str:
    """Emit a verdict array correct on ~frac_correct of items, errors spread evenly."""
    cutoff = round(frac_correct * 10)
    classifications = []
    for idx, true_label in gold.items():
        label = true_label if (idx % 10) < cutoff else 1 - true_label
        classifications.append({"idx": idx, "label": "injection" if label == 1 else "safe"})
    return json.dumps({"classifications": classifications})


@pytest.mark.golden
def test_perfect_predictions_score_one() -> None:
    gold = _gold(40)
    score = ClassificationGrader().grade(output=_output(gold, 1.0), gold={"labels": gold})
    assert score.value == 1.0
    assert score.status == "ok"
    assert score.subscores["f1"] == 1.0


@pytest.mark.golden
def test_all_wrong_predictions_score_zero() -> None:
    gold = _gold(40)
    score = ClassificationGrader().grade(output=_output(gold, 0.0), gold={"labels": gold})
    assert score.value == 0.0


@pytest.mark.golden
def test_shuffled_label_control_collapses_to_chance() -> None:
    # The leakage gate: destroying label↔prediction alignment must yield ~0.5.
    gold = _gold(40)
    score = ClassificationGrader().grade(output=_output(gold, 1.0), gold={"labels": gold})
    assert abs(score.subscores["shuffled_auroc"] - 0.5) < 0.15


@pytest.mark.golden
def test_bootstrap_ci_present_and_brackets_value_when_nondegenerate() -> None:
    gold = _gold(40)
    score = ClassificationGrader().grade(output=_output(gold, 0.8), gold={"labels": gold})
    assert score.value == pytest.approx(0.8, abs=0.05)
    assert "ci_low" in score.subscores
    assert score.subscores["ci_low"] <= score.value <= score.subscores["ci_high"]


@pytest.mark.unit
def test_missing_predictions_are_penalised() -> None:
    gold = _gold(20)
    items = json.loads(_output(gold, 1.0))["classifications"][:-5]
    score = ClassificationGrader().grade(
        output=json.dumps({"classifications": items}), gold={"labels": gold}
    )
    assert score.value < 1.0
    assert len(score.details["missing_idx"]) == 5


@pytest.mark.unit
def test_numeric_labels_accepted() -> None:
    gold = _gold(20)
    preds = [{"idx": i, "label": gold[i]} for i in gold]  # 0/1 ints, not strings
    score = ClassificationGrader().grade(
        output=json.dumps({"classifications": preds}), gold={"labels": gold}
    )
    assert score.value == 1.0


@pytest.mark.unit
def test_garbage_output_is_unparseable() -> None:
    gold = _gold(20)
    assert ClassificationGrader().grade(output="not json", gold={"labels": gold}).status == (
        "unparseable"
    )


@pytest.mark.unit
def test_no_gold_is_grader_error() -> None:
    score = ClassificationGrader().grade(output='{"classifications":[]}', gold={"labels": {}})
    assert score.status == "grader_error"


@pytest.mark.unit
def test_min_bootstrap_n_is_ten() -> None:
    assert MIN_BOOTSTRAP_N == 10


@pytest.mark.unit
def test_single_class_gold_falls_back_to_accuracy() -> None:
    gold = {0: 1, 1: 1, 2: 1, 3: 1}  # AUROC undefined on a single class
    preds = [{"idx": i, "label": "injection"} for i in gold]
    score = ClassificationGrader().grade(
        output=json.dumps({"classifications": preds}), gold={"labels": gold}
    )
    assert math.isnan(score.subscores["auroc"])
    assert score.value == 1.0  # falls back to accuracy
    assert score.status == "ok"


@pytest.mark.unit
def test_non_mapping_gold_is_grader_error() -> None:
    score = ClassificationGrader().grade(
        output='{"classifications":[]}', gold={"labels": "not-a-dict"}
    )
    assert score.status == "grader_error"


@pytest.mark.unit
def test_noninteger_gold_keys_are_skipped() -> None:
    score = ClassificationGrader().grade(
        output='{"classifications":[{"idx":0,"label":"safe"}]}', gold={"labels": {"x": 1}}
    )
    assert score.status == "grader_error"  # no usable gold labels


@pytest.mark.unit
def test_extra_predictions_are_recorded() -> None:
    gold = _gold(10)
    preds = [{"idx": i, "label": "injection" if gold[i] == 1 else "safe"} for i in range(10)]
    preds += [{"idx": 10, "label": "safe"}, {"idx": 11, "label": "safe"}]
    score = ClassificationGrader().grade(
        output=json.dumps({"classifications": preds}), gold={"labels": gold}
    )
    assert sorted(score.details["extra_idx"]) == [10, 11]


@pytest.mark.unit
def test_dict_without_list_key_is_unparseable() -> None:
    score = ClassificationGrader().grade(output='{"foo": 1}', gold={"labels": _gold(10)})
    assert score.status == "unparseable"


@pytest.mark.unit
def test_bare_list_verdicts_accepted() -> None:
    gold = _gold(10)
    preds = [{"idx": i, "label": "injection" if gold[i] == 1 else "safe"} for i in gold]
    score = ClassificationGrader().grade(output=json.dumps(preds), gold={"labels": gold})
    assert score.value == 1.0


@pytest.mark.unit
def test_malformed_items_are_skipped() -> None:
    gold = _gold(10)
    items = [
        {"idx": 0, "label": "injection"},
        "garbage",  # not a dict
        {"label": "safe"},  # no idx
        {"idx": "x", "label": "safe"},  # non-integer idx
    ]
    score = ClassificationGrader().grade(
        output=json.dumps({"classifications": items}), gold={"labels": gold}
    )
    assert score.status == "ok"  # one usable prediction; the rest are skipped/missing


@pytest.mark.unit
def test_bool_and_float_and_string_labels_coerced() -> None:
    gold = {0: "injection", 1: "safe"}  # string gold labels
    preds = [{"idx": 0, "label": True}, {"idx": 1, "label": 0.1}]  # bool + float preds
    score = ClassificationGrader().grade(
        output=json.dumps({"classifications": preds}), gold={"labels": gold}
    )
    assert score.value == 1.0
