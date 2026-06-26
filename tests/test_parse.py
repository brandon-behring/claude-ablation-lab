"""Shared grader parsing helpers — lenient_json + parse_verdict."""

from __future__ import annotations

import pytest

from claude_ablation_lab.graders._parse import lenient_json, parse_verdict


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("injection", (1, False)),
        ("Injection detected", (1, False)),
        ("safe", (0, False)),
        ("SAFE!", (0, False)),
        ("this looks like an injection attempt", (1, False)),
        ("clearly safe content", (0, False)),
        ("could be injection or safe", (0, True)),  # both present → ambiguous
        ("maybe?", (0, True)),
        ("", (0, True)),
    ],
)
def test_parse_verdict(raw: str, expected: tuple[int, bool]) -> None:
    assert parse_verdict(raw) == expected


@pytest.mark.unit
def test_parse_verdict_non_string() -> None:
    assert parse_verdict(None) == (0, True)  # type: ignore[arg-type]


@pytest.mark.unit
def test_lenient_json_object() -> None:
    assert lenient_json('{"a": 1}') == {"a": 1}


@pytest.mark.unit
def test_lenient_json_array() -> None:
    assert lenient_json("[1, 2, 3]") == [1, 2, 3]


@pytest.mark.unit
def test_lenient_json_object_with_preamble() -> None:
    assert lenient_json('Note:\n{"a": 1}\nbye') == {"a": 1}


@pytest.mark.unit
def test_lenient_json_outer_array_wins_over_inner_object() -> None:
    assert lenient_json('x [{"k": 1}] y') == [{"k": 1}]


@pytest.mark.unit
def test_lenient_json_trailing_brace_chatter() -> None:
    # first-{…last-} slicing would over-capture the stray brace and fail; raw_decode stops early.
    assert lenient_json('{"a": 1} }') == {"a": 1}


@pytest.mark.unit
def test_lenient_json_garbage_returns_none() -> None:
    assert lenient_json("no json here") is None
