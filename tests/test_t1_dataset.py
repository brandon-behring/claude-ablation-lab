"""T1 dataset gaps the live sweep depends on: the ``--json-schema`` shape, the
batched-prompt structure, the idx→gold positional contract, and the
``$T1_HOLDOUT_PATH`` override. ``tests/test_task.py`` already covers ``subsample``
balance/seed-stability and ``load_holdout``, so those are not repeated here.
"""

from __future__ import annotations

import importlib

import pandas as pd
import pytest

from claude_ablation_lab import t1_dataset as t1


def _frame(n_pos: int = 5, n_neg: int = 5) -> pd.DataFrame:
    """A synthetic balanced holdout with distinguishable text per row."""
    rows = [{"text": f"inject-{i}", "label": 1} for i in range(n_pos)]
    rows += [{"text": f"benign-{i}", "label": 0} for i in range(n_neg)]
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_verdict_schema_shape() -> None:
    # This schema is handed to `claude -p --json-schema` on every live T1 cell; a
    # malformed shape breaks the grader silently, so pin it.
    schema = t1.VERDICT_JSON_SCHEMA
    assert schema["required"] == ["classifications"]
    item = schema["properties"]["classifications"]["items"]
    assert item["properties"]["idx"]["type"] == "integer"
    assert item["properties"]["label"]["enum"] == ["injection", "safe"]
    assert set(item["required"]) == {"idx", "label"}


@pytest.mark.unit
def test_build_prompt_has_one_contiguous_msg_per_row() -> None:
    frame = t1.subsample(_frame(20, 20), n=10)
    prompt = t1.build_prompt(frame)
    for k in range(10):  # exactly one <msg idx=k> per row, idx 0..n-1 contiguous
        assert prompt.count(f"<msg idx={k}>") == 1
    assert "<msg idx=10>" not in prompt  # no off-by-one 11th message
    # Exact structure (the earlier `>= 10` masked a missing closer — review consensus):
    # every real opener is immediately paired with a closer after its payload line,
    # plus exactly one `</msg>` inside the delimiter-explanation prose line.
    import re

    blocks = re.findall(r"<msg idx=(\d+)>\n.*?\n</msg>", prompt, flags=re.DOTALL)
    assert [int(b) for b in blocks] == list(range(10))
    assert prompt.count("</msg>") == 11  # 10 real closers + the explanation line's literal


@pytest.mark.unit
def test_build_prompt_embeds_instructions_and_contract() -> None:
    prompt = t1.build_prompt(t1.subsample(_frame(), n=4))
    assert t1.JUDGE_INSTRUCTIONS in prompt
    assert "untrusted DATA" in prompt  # the data-not-instructions hardening line
    assert '"classifications"' in prompt and "one entry per message idx" in prompt


@pytest.mark.unit
def test_gold_keys_and_values_align_positionally() -> None:
    # The grader scores by positional idx, so build_gold's keys must be 0..n-1 and
    # each value must equal the reset-indexed frame's label at that position.
    frame = t1.subsample(_frame(30, 30), n=12)
    gold = t1.build_gold(frame)
    assert set(gold.keys()) == set(range(12))
    assert all(gold[i] == int(frame["label"].iloc[i]) for i in range(12))


@pytest.mark.unit
def test_holdout_path_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T1_HOLDOUT_PATH", "/tmp/custom/holdout.parquet")
    try:
        reloaded = importlib.reload(t1)
        assert str(reloaded.DEFAULT_HOLDOUT_PATH) == "/tmp/custom/holdout.parquet"
    finally:
        # undo() restores the PRE-TEST environment BEFORE the reload — a plain delenv
        # would bake the fallback default in for the rest of the session on machines
        # where the user genuinely has T1_HOLDOUT_PATH set (review finding).
        monkeypatch.undo()
        importlib.reload(t1)
