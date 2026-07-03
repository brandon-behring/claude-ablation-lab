"""books-validate grader — the adversarial battery.

One test per attack the pre-build design review raised: deletion/empty must not beat honest work,
census excess-only kills spraying, the summary is trusted only from the final line cross-checked
against the exit code (no echo-injection), degenerate outputs score a real 0.0 (not grader_error,
which would exclude them from the mean), and fenced/prose-wrapped/double-printed replies unwrap to
the chapter. Uses the SHIPPED fixture (examples/books-validate) — no skip, it is in the repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_ablation_lab.graders.books_validate import (
    DEFAULT_FIXTURE_ROOT,
    BooksValidateGrader,
    _score_from_summary,
    extract_chapter,
)

_ANCHOR = "The Nonparametric Bootstrap"
_GOLD = (Path(__file__).parent / "fixtures" / "books_validate_gold.mdx").read_text(encoding="utf-8")
_SEEDED = (DEFAULT_FIXTURE_ROOT / "chapter.mdx").read_text(encoding="utf-8")


def _grade(output: str, gold: dict | None = None):
    return BooksValidateGrader().grade(output=output, gold=gold or {})


# --- the gradient: empty < delete < seeded < gold ----------------------------


@pytest.mark.unit
def test_gold_scores_one() -> None:
    s = _grade(_GOLD)
    assert s.value == 1.0 and s.status == "ok"


@pytest.mark.unit
def test_seeded_scores_the_pinned_half() -> None:
    assert _grade(_SEEDED).value == pytest.approx(0.5)  # 7.5 / 15


@pytest.mark.unit
def test_empty_scores_zero_not_grader_error() -> None:
    s = _grade("")
    assert s.value == 0.0 and s.status == "ok"  # a real quality-0, stays in the epoch mean


@pytest.mark.unit
def test_deleting_every_tag_does_not_beat_honest_work() -> None:
    import re

    stripped = re.sub(r"<[^>]+>", "", _SEEDED)
    assert _grade(stripped).value <= _grade(_SEEDED).value
    assert _grade(stripped).value == 0.0


@pytest.mark.unit
def test_spraying_valid_ids_fails_the_census() -> None:
    sprayed = (
        _GOLD + '\n\nSee <XRef id="sec-intro"/> <XRef id="sec-conclusion"/> <XRef id="thm-clt"/>.\n'
    )
    assert _grade(sprayed).value < 1.0  # census_xref count > max


@pytest.mark.unit
def test_fixing_only_fidelity_visible_lands_between_seeded_and_gold() -> None:
    # A model that only clears validate_fixture.py (unknown id/key/range/structural) but misses the
    # semantic near-misses and the required addition must score strictly between 0.5 and 1.0.
    fid = _SEEDED
    for a, b in [
        ('<BookLink book="handbook">', '<BookLink book="handbook" to="/x">'),
        ('<Cite key="efron1978"/>', '<Cite key="efron1979"/>'),
        ('<XRef id="sec-bootstrap-basic"/>', '<XRef id="sec-bootstrap-basics"/>'),
        ("line={200} lineEnd={240}", "line={30} lineEnd={70}"),
    ]:
        fid = fid.replace(a, b)
    assert 0.5 < _grade(fid).value < 1.0


# --- degenerate, model-controlled outputs -> deterministic 0.0 (status ok) ----


@pytest.mark.unit
def test_oversize_output_scores_zero_ok() -> None:
    s = _grade("x" * 1_000_001)
    assert s.value == 0.0 and s.status == "ok" and s.details["reason"] == "oversize"


@pytest.mark.unit
def test_binary_nul_output_scores_zero_ok() -> None:
    s = _grade(_GOLD + "\x00")
    assert s.value == 0.0 and s.status == "ok" and "binary" in s.details["reason"]


@pytest.mark.unit
def test_missing_checker_is_grader_error(tmp_path: Path) -> None:
    s = BooksValidateGrader(fixture_root=tmp_path).grade(
        output=_GOLD, gold={"fixture_root": tmp_path}
    )
    assert s.status == "grader_error"


# --- summary parsing: final line only, cross-checked against the exit code ----


@pytest.mark.unit
def test_pass_summary_requires_exit_zero_and_full_points() -> None:
    assert _score_from_summary(0, "CHECK PASSED: 15/15 points").value == 1.0
    # PASSED text but a nonzero exit = check.py disagreeing with itself -> grader_error, never 1.0.
    assert _score_from_summary(3, "CHECK PASSED: 15/15 points").status == "grader_error"


@pytest.mark.unit
def test_fail_summary_must_match_exit_code() -> None:
    ok = _score_from_summary(10, "CHECK FAILED: 7.5/15 points, 10 items below full")
    assert ok.value == pytest.approx(0.5) and ok.status == "ok"
    bad = _score_from_summary(0, "CHECK FAILED: 7.5/15 points, 10 items below full")
    assert bad.status == "grader_error"  # count/exit disagree


@pytest.mark.unit
def test_echo_injection_cannot_forge_a_verdict() -> None:
    # A per-item line echoes a submitted value literally equal to a PASS summary; only the FINAL
    # line is parsed, so the real FAILED verdict stands.
    stdout = (
        "item xref_bca [fuzzy]: 0.0  (id='CHECK PASSED: 15/15 points')\n"
        "CHECK FAILED: 7.5/15 points, 10 items below full"
    )
    assert _score_from_summary(10, stdout).value == pytest.approx(0.5)


@pytest.mark.unit
def test_unparseable_and_empty_checker_output_are_grader_error() -> None:
    assert _score_from_summary(0, "some traceback\nRuntimeError").status == "grader_error"
    assert _score_from_summary(0, "").status == "grader_error"


# --- fence / wrapper extraction ----------------------------------------------


@pytest.mark.unit
def test_extract_plain_chapter_is_identity() -> None:
    assert extract_chapter(_GOLD, _ANCHOR) == _GOLD


@pytest.mark.unit
def test_extract_unwraps_single_fenced_block() -> None:
    assert extract_chapter(f"```mdx\n{_GOLD}\n```", _ANCHOR) == _GOLD


@pytest.mark.unit
def test_extract_unwraps_prose_then_fence() -> None:
    wrapped = f"Sure, here is the corrected chapter:\n\n```\n{_GOLD}\n```\n\nHope that helps!"
    assert extract_chapter(wrapped, _ANCHOR) == _GOLD


@pytest.mark.unit
def test_fenced_and_prose_wrapped_gold_still_scores_one() -> None:
    assert _grade(f"Here you go:\n```mdx\n{_GOLD}\n```").value == 1.0


@pytest.mark.unit
def test_double_printed_chapter_grades_one_block_not_the_doubled_counts() -> None:
    # Two copies would double every census count if concatenated; extraction takes one block.
    assert _grade(f"```\n{_GOLD}\n```\n\nfinal:\n```\n{_GOLD}\n```").value == 1.0


@pytest.mark.unit
def test_diff_shaped_submission_scores_low_without_crashing() -> None:
    diff = '--- a/chapter.mdx\n+++ b/chapter.mdx\n@@\n-<Cite key="efron1978"/>\n+<Cite key="efron1979"/>\n'
    s = _grade(diff)
    assert s.status == "ok" and 0.0 <= s.value < 0.5  # instruction-following failure, deterministic


# --- regressions for the 3-voice review findings -----------------------------


@pytest.mark.unit
def test_commented_out_tags_score_zero_not_full() -> None:
    # Confirmed exploit: commenting out every tag rendered the chapter inert yet scored 15/15,
    # because the regex counted tags inside comments. check.py now strips comments first.
    import re

    commented = re.sub(r"(<(?:XRef|Cite|CodeRef|BookLink)\b[^>]*>)", r"<!-- \1 -->", _GOLD)
    assert _grade(commented).value == 0.0


@pytest.mark.unit
def test_tags_inside_code_fences_are_not_counted() -> None:
    # A trailing explanation code-block full of tag-like text must not inflate the census.
    noisy = (
        _GOLD
        + '\n\nHere is what I changed:\n```\n<XRef id="sec-intro"/> <Cite key="hall1992"/>\n```\n'
    )
    assert _grade(noisy).value == 1.0  # the fenced block is stripped before scoring


@pytest.mark.unit
def test_duplicate_anchor_preamble_is_not_credited() -> None:
    # Prepending a correct tag on a copied anchor line must NOT farm the item while the body stays
    # broken — a duplicated anchor is rejected (scores that item 0), so this ties seeded, not better.
    preamble = (
        'The bootstrap was introduced by Efron in 1979 <Cite key="efron1979"/>.\n\n' + _SEEDED
    )
    assert _grade(preamble).value <= _grade(_SEEDED).value


@pytest.mark.unit
def test_coderef_without_line_numbers_fails() -> None:
    noline = _GOLD.replace(
        '<CodeRef path="src/resample.py" line={30} lineEnd={70}/>',
        '<CodeRef path="src/resample.py"/>',
    )
    assert _grade(noline).value < 1.0  # deleting the range is an evasion, not a fix


@pytest.mark.unit
def test_coderef_repointed_to_a_wrong_valid_file_fails() -> None:
    wrong = _GOLD.replace(
        '<CodeRef path="src/resample.py" line={30} lineEnd={70}/>',
        '<CodeRef path="src/permutation.py" line={30} lineEnd={70}/>',
    )
    assert _grade(wrong).value < 1.0  # expected_path pins the file


@pytest.mark.unit
def test_brace_quoted_correct_value_is_not_an_inversion() -> None:
    # A valid MDX-expression attribute id={"thm-clt"} carries the CORRECT value and must score full,
    # not 0 for its delimiter style.
    brace = _GOLD.replace('<XRef id="thm-clt"/>', '<XRef id={"thm-clt"}/>')
    assert _grade(brace).value == 1.0


# --- the rubric hash in the version ------------------------------------------


@pytest.mark.unit
def test_version_tracks_the_rubric_files(tmp_path: Path) -> None:
    import shutil

    for name in ("expected.json", "check.py"):
        shutil.copy(DEFAULT_FIXTURE_ROOT / name, tmp_path / name)
    v1 = BooksValidateGrader(fixture_root=tmp_path).version
    (tmp_path / "expected.json").write_text(
        (tmp_path / "expected.json").read_text() + "\n", encoding="utf-8"
    )
    # No caching (a stale cache would defeat the re-grade trigger), so the edit shows immediately.
    v2 = BooksValidateGrader(fixture_root=tmp_path).version
    assert v1 != v2 and v1.startswith("books-validate-v1+fx")
