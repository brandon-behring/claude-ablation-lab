"""The anti-narration verdict scanner (judges/_parse.py)."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from claude_ablation_lab.judges._parse import extract_verdict


@pytest.mark.unit
def test_clean_json_object() -> None:
    assert extract_verdict('{"winner": "A", "reason": "tighter proof"}') == (
        "A",
        "tighter proof",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("a", "A"), ("B", "B"), ("tie", "tie"), ("TIE", "tie"), (" Tie ", "tie")],
)
def test_winner_is_case_normalized(raw: str, expected: str) -> None:
    result = extract_verdict(json.dumps({"winner": raw}))
    assert result is not None
    assert result[0] == expected


@pytest.mark.unit
def test_narration_before_and_after_the_object() -> None:
    text = (
        "Let me compare the two responses carefully.\n"
        'Here is my analysis: {"note": "this is not a verdict"}\n'
        '```json\n{"winner": "B", "reason": "covers the theorem"}\n```\n'
        "I hope that helps!"
    )
    assert extract_verdict(text) == ("B", "covers the theorem")


@pytest.mark.unit
def test_first_schema_match_wins_not_first_json() -> None:
    text = '{"answers": [1, 2]} {"winner": "tie", "reason": "equal"} {"winner": "A"}'
    assert extract_verdict(text) == ("tie", "equal")


@pytest.mark.unit
def test_braces_inside_strings_do_not_break_spans() -> None:
    text = '{"winner": "A", "reason": "uses {curly} notation and a \\" quote"}'
    result = extract_verdict(text)
    assert result is not None
    assert result[0] == "A"


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "",
        "no json at all",
        '{"winner": "C"}',  # not a valid verdict value
        '{"winner": 1}',  # wrong type
        '{"verdict": "A"}',  # wrong key
        '{"winner": "A"',  # unbalanced — never closes
    ],
)
def test_unparseable_shapes_return_none(text: str) -> None:
    assert extract_verdict(text) is None


@pytest.mark.unit
def test_verdict_object_inside_a_list_is_recovered() -> None:
    # A judge that wraps its verdict in an array still schema-matches on the
    # inner object span — robustness, not leniency (the object itself is exact).
    assert extract_verdict('[{"winner": "A", "reason": "r"}]') == ("A", "r")


@pytest.mark.unit
def test_reason_is_bounded_and_optional() -> None:
    long = json.dumps({"winner": "A", "reason": "x" * 2000})
    result = extract_verdict(long)
    assert result is not None
    assert len(result[1]) == 500
    bare = extract_verdict('{"winner": "B"}')
    assert bare == ("B", "")


@pytest.mark.property
@given(st.text(max_size=400))
def test_never_raises_on_arbitrary_text(text: str) -> None:
    result = extract_verdict(text)
    assert result is None or result[0] in ("A", "B", "tie")
