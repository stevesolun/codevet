"""Tests for confidence scoring."""

from __future__ import annotations

from codevet.models import ConfidenceScore, GeneratedTest, VetResult
from codevet.scorer import (
    calculate_pass_rate,
    parse_critique_response,
    score_fix,
)


class TestConfidenceScoring:
    """Test the confidence scoring formula: score = pass_rate * 70 + critique_score * 30."""

    def _make_vet_result(self, passed: int, failed: int, errors: int = 0) -> VetResult:
        """Helper to create a VetResult with given pass/fail counts."""

        total = passed + failed + errors
        return VetResult(
            test_cases=[
                GeneratedTest(name=f"test_{i}", code=f"def test_{i}(): pass", category="unit")
                for i in range(total)
            ],
            passed=passed,
            failed=failed,
            errors=errors,
            raw_output=f"{passed} passed, {failed} failed, {errors} error",
        )

    def test_confidence_pass_weight_70(self):
        """With pass_rate=1.0, critique_score=0.0, verify score == 70."""
        from codevet.scorer import score_fix

        vet_result = self._make_vet_result(passed=10, failed=0)
        # Mock critique response that yields critique_score=0.0
        critique_response = '{"score": 0, "reasoning": "no critique"}'
        result = score_fix(vet_result, critique_response)
        assert isinstance(result, ConfidenceScore)
        assert result.score == 70

    def test_confidence_critique_weight_30(self):
        """With pass_rate=0.0, critique_score=1.0, verify score == 30."""
        from codevet.scorer import score_fix

        vet_result = self._make_vet_result(passed=0, failed=10)
        # Mock critique response that yields critique_score=1.0
        critique_response = '{"score": 100, "reasoning": "perfect critique"}'
        result = score_fix(vet_result, critique_response)
        assert isinstance(result, ConfidenceScore)
        assert result.score == 30

    def test_confidence_range_0_100(self):
        """Test extreme values: both 1.0 -> 100, both 0.0 -> 0."""
        from codevet.scorer import score_fix

        # Both maximums -> 100
        vet_max = self._make_vet_result(passed=10, failed=0)
        critique_max = '{"score": 100, "reasoning": "perfect"}'
        result_max = score_fix(vet_max, critique_max)
        assert result_max.score == 100

        # Both minimums -> 0
        vet_min = self._make_vet_result(passed=0, failed=10)
        critique_min = '{"score": 0, "reasoning": "terrible"}'
        result_min = score_fix(vet_min, critique_min)
        assert result_min.score == 0

    def test_confidence_zero_passes(self):
        """With pass_rate=0.0 and critique_score=0.5, verify score == 15."""
        from codevet.scorer import score_fix

        vet_result = self._make_vet_result(passed=0, failed=10)
        critique_response = '{"score": 50, "reasoning": "half"}'
        result = score_fix(vet_result, critique_response)
        assert isinstance(result, ConfidenceScore)
        assert result.score == 15

    def test_score_fix_enriches_explanation(self):
        """score_fix appends critique reasoning to the explanation when available."""
        vet_result = self._make_vet_result(passed=5, failed=5)
        critique_response = '{"score": 60, "reasoning": "Partially correct fix."}'
        result = score_fix(vet_result, critique_response)
        assert "Critique: Partially correct fix." in result.explanation


class TestParseCritiqueResponse:
    """Test parse_critique_response edge cases."""

    def test_empty_response(self):
        """Empty string returns fallback score 0.5."""
        score, reasoning = parse_critique_response("")
        assert score == 0.5
        assert "could not be parsed" in reasoning

    def test_malformed_json(self):
        """Malformed JSON returns fallback score 0.5."""
        score, reasoning = parse_critique_response("not json at all")
        assert score == 0.5

    def test_non_dict_json(self):
        """JSON array returns fallback score 0.5."""
        score, reasoning = parse_critique_response("[1, 2, 3]")
        assert score == 0.5

    def test_missing_score_key(self):
        """JSON without 'score' key returns fallback score 0.5."""
        score, reasoning = parse_critique_response('{"reasoning": "no score here"}')
        assert score == 0.5

    def test_non_integer_score(self):
        """Non-integer score returns fallback 0.5."""
        score, reasoning = parse_critique_response('{"score": "abc", "reasoning": "bad"}')
        assert score == 0.5

    def test_markdown_fenced_json(self):
        """JSON wrapped in markdown fences is still parsed correctly."""
        response = '```json\n{"score": 80, "reasoning": "good"}\n```'
        score, reasoning = parse_critique_response(response)
        assert score == 0.8
        assert reasoning == "good"

    def test_score_clamped_to_100(self):
        """Score > 100 is clamped to 1.0."""
        score, _ = parse_critique_response('{"score": 200, "reasoning": "over"}')
        assert score == 1.0

    def test_score_clamped_to_0(self):
        """Score < 0 is clamped to 0.0."""
        score, _ = parse_critique_response('{"score": -50, "reasoning": "under"}')
        assert score == 0.0


class TestCalculatePassRate:
    """Test calculate_pass_rate edge cases."""

    def test_zero_total(self):
        """Zero total tests returns 0.0 pass rate."""
        vet_result = VetResult(
            test_cases=[],
            passed=0,
            failed=0,
            errors=0,
            raw_output="",
        )
        assert calculate_pass_rate(vet_result) == 0.0
