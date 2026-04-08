"""Tests for codevet Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codevet.models import (
    ConfidenceScore,
    FixResult,
    GeneratedTest,
    SandboxConfig,
    VetResult,
)


class TestSandboxConfigModel:
    """SandboxConfig default values."""

    def test_sandbox_config_model(self, tmp_path):
        """Default SandboxConfig has correct defaults."""
        config = SandboxConfig(project_dir=tmp_path)
        assert config.read_only is True
        assert config.network_disabled is True
        assert config.timeout_seconds == 120


class TestVetResultModel:
    """VetResult computed fields."""

    def test_vet_result_model(self):
        """VetResult.total computed from test_cases length."""
        cases = [
            GeneratedTest(name="test_a", code="def test_a(): pass", category="unit"),
            GeneratedTest(name="test_b", code="def test_b(): pass", category="edge"),
            GeneratedTest(name="test_c", code="def test_c(): pass", category="security"),
        ]
        result = VetResult(
            test_cases=cases,
            passed=2,
            failed=1,
            errors=0,
            raw_output="2 passed, 1 failed",
        )
        assert result.total == 3


class TestConfidenceScoreModel:
    """ConfidenceScore clamping behaviour."""

    def test_score_model_range(self):
        """ConfidenceScore.score is clamped to 0-100."""
        high = ConfidenceScore(
            score=150,
            pass_rate=1.0,
            critique_score=1.0,
            explanation="test",
        )
        assert high.score == 100

        low = ConfidenceScore(
            score=-10,
            pass_rate=0.0,
            critique_score=0.0,
            explanation="test",
        )
        assert low.score == 0


class TestFixResultModel:
    """FixResult validation."""

    def test_fix_result_model(self):
        """FixResult with iterations_used > 3 raises validation error."""
        VetResult(
            test_cases=[],
            passed=0,
            failed=0,
            errors=0,
            raw_output="",
        )
        with pytest.raises(ValidationError, match="iterations_used must not exceed 3"):
            FixResult(
                original_code="pass",
                fixed_code="pass",
                attempts=[],
                success=False,
                iterations_used=4,
            )


class TestConfidenceScoreValidateRates:
    """ConfidenceScore validate_rates rejection."""

    def test_pass_rate_above_1_rejected(self):
        """pass_rate > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError, match="pass_rate must be between"):
            ConfidenceScore(
                score=50,
                pass_rate=1.5,
                critique_score=0.5,
                explanation="test",
            )

    def test_pass_rate_below_0_rejected(self):
        """pass_rate < 0.0 raises ValidationError."""
        with pytest.raises(ValidationError, match="pass_rate must be between"):
            ConfidenceScore(
                score=50,
                pass_rate=-0.1,
                critique_score=0.5,
                explanation="test",
            )

    def test_critique_score_above_1_rejected(self):
        """critique_score > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError, match="critique_score must be between"):
            ConfidenceScore(
                score=50,
                pass_rate=0.5,
                critique_score=1.1,
                explanation="test",
            )

    def test_critique_score_below_0_rejected(self):
        """critique_score < 0.0 raises ValidationError."""
        with pytest.raises(ValidationError, match="critique_score must be between"):
            ConfidenceScore(
                score=50,
                pass_rate=0.5,
                critique_score=-0.1,
                explanation="test",
            )


class TestConfidenceScoreGrade:
    """ConfidenceScore grade property."""

    def test_grade_a(self):
        """Score >= 90 is grade A."""
        s = ConfidenceScore(score=95, pass_rate=1.0, critique_score=1.0, explanation="test")
        assert s.grade == "A"

    def test_grade_b(self):
        """Score 80-89 is grade B."""
        s = ConfidenceScore(score=85, pass_rate=0.9, critique_score=0.7, explanation="test")
        assert s.grade == "B"

    def test_grade_c(self):
        """Score 70-79 is grade C."""
        s = ConfidenceScore(score=75, pass_rate=0.8, critique_score=0.6, explanation="test")
        assert s.grade == "C"

    def test_grade_d(self):
        """Score 60-69 is grade D."""
        s = ConfidenceScore(score=65, pass_rate=0.7, critique_score=0.5, explanation="test")
        assert s.grade == "D"

    def test_grade_f(self):
        """Score < 60 is grade F."""
        s = ConfidenceScore(score=40, pass_rate=0.4, critique_score=0.3, explanation="test")
        assert s.grade == "F"
