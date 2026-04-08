"""Comprehensive test suite for codevet — 30 tests across 4 difficulty tiers.

Tier 1 (1-10):   Light — basic happy paths
Tier 2 (11-20):  Medium — real scenarios, mocked externals
Tier 3 (21-25):  Tricky — edge cases and boundary conditions
Tier 4 (26-30):  Super Tricky — break the implementation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codevet.fixer import Fixer
from codevet.models import (
    ConfidenceScore,
    FixResult,
    GeneratedTest,
    SandboxConfig,
    VetResult,
)
from codevet.prompts import build_fix_prompt, build_test_generation_prompt
from codevet.sandbox import DockerSandboxError, Sandbox
from codevet.scorer import calculate_confidence, calculate_pass_rate, parse_critique_response
from codevet.utils import format_diff
from codevet.vetter import (
    _categorise,
    combine_test_cases,
    parse_pytest_output,
)

# ======================================================================
# TIER 1: LIGHT (tests 1-10) — Basic happy paths
# ======================================================================


class TestTier1Light:
    """Basic happy-path tests verifying sane defaults and core behaviour."""

    def test_models_sandbox_config_defaults(self, tmp_path: Path) -> None:
        """SandboxConfig has sane defaults for image, timeout, and security."""
        config = SandboxConfig(project_dir=tmp_path)
        assert config.image == "python:3.11-slim"
        assert config.timeout_seconds == 120
        assert config.read_only is True
        assert config.network_disabled is True
        assert "/tmp" in config.tmpfs_mounts
        assert "no-new-privileges" in config.security_opt

    def test_models_generated_test_categories(self) -> None:
        """All 4 category literals are accepted by GeneratedTest."""
        categories = ("unit", "edge", "security", "performance")
        for cat in categories:
            gt = GeneratedTest(name=f"test_{cat}", code="pass", category=cat)
            assert gt.category == cat

    def test_models_confidence_score_grade_A(self) -> None:
        """A score of 95 yields grade 'A'."""
        cs = ConfidenceScore(
            score=95, pass_rate=0.95, critique_score=0.95, explanation="great"
        )
        assert cs.grade == "A"

    def test_models_confidence_score_grade_F(self) -> None:
        """A score of 20 yields grade 'F'."""
        cs = ConfidenceScore(
            score=20, pass_rate=0.2, critique_score=0.2, explanation="poor"
        )
        assert cs.grade == "F"

    def test_scorer_perfect_score(self) -> None:
        """pass_rate=1.0 and critique=1.0 produce a score of 100."""
        result = calculate_confidence(1.0, 1.0)
        assert result.score == 100

    def test_scorer_zero_score(self) -> None:
        """pass_rate=0.0 and critique=0.0 produce a score of 0."""
        result = calculate_confidence(0.0, 0.0)
        assert result.score == 0

    def test_prompts_test_gen_returns_tuple(self) -> None:
        """build_test_generation_prompt returns a 2-tuple of strings."""
        system, user = build_test_generation_prompt("x = 1", "example.py")
        assert isinstance(system, str)
        assert isinstance(user, str)
        assert len(system) > 0
        assert len(user) > 0

    def test_prompts_fix_returns_tuple(self) -> None:
        """build_fix_prompt returns a 2-tuple of strings."""
        system, user = build_fix_prompt(
            code="x = 1", test_code="assert x == 1", error_output="FAILED", iteration=1
        )
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_utils_format_diff_identical(self) -> None:
        """Identical strings produce an empty diff."""
        text = "hello\nworld\n"
        diff = format_diff(text, text)
        assert diff == ""

    def test_utils_format_diff_one_line_change(self) -> None:
        """A single line change produces a valid unified diff."""
        original = "hello\nworld\n"
        fixed = "hello\nearth\n"
        diff = format_diff(original, fixed)
        assert "---" in diff
        assert "+++" in diff
        assert "-world" in diff
        assert "+earth" in diff


# ======================================================================
# TIER 2: MEDIUM (tests 11-20) — Real scenarios, mocked externals
# ======================================================================


class TestTier2Medium:
    """Tests requiring knowledge of real module behaviour with mocked externals."""

    def test_vetter_categorize_security(self) -> None:
        """A function name containing 'security' maps to the security category."""
        assert _categorise("test_sql_injection_security_check") == "security"

    def test_vetter_categorize_edge(self) -> None:
        """A function name containing 'empty' maps to the edge category."""
        assert _categorise("test_handle_empty_input") == "edge"

    def test_vetter_parse_pytest_all_pass(self) -> None:
        """'10 passed' parses to (10, 0, 0)."""
        output = "===== 10 passed in 0.42s ====="
        assert parse_pytest_output(output) == (10, 0, 0)

    def test_vetter_parse_pytest_mixed(self) -> None:
        """'3 passed, 2 failed, 1 error' parses to (3, 2, 1)."""
        output = "===== 3 passed, 2 failed, 1 error in 1.23s ====="
        assert parse_pytest_output(output) == (3, 2, 1)

    def test_vetter_parse_pytest_no_match(self) -> None:
        """Garbage output falls back to (0, 0, 0)."""
        assert parse_pytest_output("no useful info here") == (0, 0, 0)

    def test_fixer_syntax_validation_good(self) -> None:
        """Valid Python string passes syntax validation."""
        fixer = Fixer.__new__(Fixer)
        assert fixer._validate_syntax("x = 1\nprint(x)") is True

    def test_fixer_syntax_validation_bad(self) -> None:
        """Invalid Python 'def foo(:' fails syntax validation."""
        fixer = Fixer.__new__(Fixer)
        assert fixer._validate_syntax("def foo(:") is False

    def test_sandbox_raises_on_no_docker(self) -> None:
        """When Docker is not running, _get_client raises DockerSandboxError."""
        from docker.errors import DockerException

        config = SandboxConfig(project_dir=Path("."))
        sandbox = Sandbox(config)
        with patch(
            "codevet.sandbox.docker.from_env",
            side_effect=DockerException("connection refused"),
        ), pytest.raises(DockerSandboxError, match="Docker is not running"):
            sandbox._get_client()

    def test_scorer_malformed_json_critique(self) -> None:
        """parse_critique_response with '{broken' returns fallback (0.5, ...)."""
        score, reasoning = parse_critique_response("{broken")
        assert score == 0.5
        assert "could not be parsed" in reasoning

    def test_cli_version_command(self) -> None:
        """'codevet version' prints the version string."""
        from typer.testing import CliRunner

        from codevet import __version__
        from codevet.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.output


# ======================================================================
# TIER 3: TRICKY (tests 21-25) — Edge cases and boundary conditions
# ======================================================================


class TestTier3Tricky:
    """Edge cases and boundary conditions that test implementation robustness."""

    def test_models_confidence_clamp_above_100(self) -> None:
        """ConfidenceScore with raw score >100 gets clamped to 100."""
        cs = ConfidenceScore(
            score=150, pass_rate=1.0, critique_score=1.0, explanation="over"
        )
        assert cs.score == 100

    def test_models_fix_result_zero_iterations(self) -> None:
        """FixResult with 0 iterations is valid (early exit, nothing to fix)."""
        fr = FixResult(
            original_code="x = 1",
            fixed_code="x = 1",
            attempts=[],
            success=True,
            iterations_used=0,
        )
        assert fr.iterations_used == 0
        assert fr.success is True
        assert fr.attempts == []

    def test_scorer_critique_with_markdown_fences(self) -> None:
        """parse_critique_response handles ```json ... ``` wrapped JSON."""
        raw = '```json\n{"score": 80, "reasoning": "Good fix"}\n```'
        score, reasoning = parse_critique_response(raw)
        assert score == 0.8
        assert reasoning == "Good fix"

    def test_vetter_combine_deduplicates_imports(self) -> None:
        """combine_test_cases with duplicate imports produces each import once."""
        tc1 = GeneratedTest(
            name="test_a",
            code="import pytest\nimport os\ndef test_a():\n    assert True\n",
            category="unit",
        )
        tc2 = GeneratedTest(
            name="test_b",
            code="import pytest\nimport os\ndef test_b():\n    assert True\n",
            category="unit",
        )
        combined = combine_test_cases([tc1, tc2])
        # Count occurrences of 'import os' — should be exactly 1
        assert combined.count("import os") == 1
        # Count occurrences of 'import pytest' — should be exactly 1
        assert combined.count("import pytest") == 1

    def test_prompts_fix_iteration_context(self) -> None:
        """iteration=3 prompt says 'attempt 3 of 3'."""
        _, user = build_fix_prompt(
            code="x = 1",
            test_code="assert x == 2",
            error_output="AssertionError",
            iteration=3,
        )
        assert "attempt 3 of 3" in user
        assert "different approach" in user.lower() or "Previous attempts failed" in user


# ======================================================================
# TIER 4: SUPER TRICKY (tests 26-30) — Break the implementation
# ======================================================================


class TestTier4SuperTricky:
    """Tests designed to break common implementation assumptions."""

    def test_models_vet_result_empty_test_cases(self) -> None:
        """VetResult with test_cases=[] has total=0 and can have passed=0."""
        vr = VetResult(
            test_cases=[],
            passed=0,
            failed=0,
            errors=0,
            raw_output="no tests ran",
        )
        assert vr.total == 0
        assert vr.passed == 0
        # Also verify calculate_pass_rate handles 0 total without ZeroDivisionError
        rate = calculate_pass_rate(vr)
        assert rate == 0.0

    def test_fixer_extract_code_nested_fences(self) -> None:
        """Response with nested ```python blocks extracts the longest one."""
        fixer = Fixer.__new__(Fixer)
        fixer.model = "test"
        fixer._client = None

        response = (
            "Here is an explanation:\n"
            "```python\nshort = 1\n```\n"
            "And the full fixed file:\n"
            "```python\n"
            "# Fixed file\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "```\n"
            "That should work.\n"
        )
        extracted = fixer._extract_code(response)
        # The implementation picks max(matches, key=len), so the longer block wins
        assert "def add(a, b):" in extracted
        assert "return a + b" in extracted

    def test_scorer_critique_score_exactly_boundary(self) -> None:
        """pass_rate=0.0, critique_score=1.0 yields exactly 30 (the 30% weight)."""
        result = calculate_confidence(0.0, 1.0)
        # Formula: int(0.0 * 0.7 * 100 + 1.0 * 0.3 * 100) = int(30.0) = 30
        assert result.score == 30

    def test_vetter_parse_output_with_warnings(self) -> None:
        """pytest output with DeprecationWarnings mixed in still parses correctly."""
        output = (
            "test_code.py::test_one PASSED\n"
            "test_code.py::test_two FAILED\n"
            "/usr/lib/python3.11/site-packages/foo.py:12: DeprecationWarning: "
            "use bar() instead\n"
            "  warnings.warn('use bar() instead', DeprecationWarning)\n"
            "===== 5 passed, 3 failed, 1 error in 2.34s =====\n"
        )
        passed, failed, errors = parse_pytest_output(output)
        assert passed == 5
        assert failed == 3
        assert errors == 1

    def test_sandbox_context_manager_cleanup(self) -> None:
        """Sandbox used as context manager calls close on exit even if no run()."""
        config = SandboxConfig(project_dir=Path("."))
        sandbox = Sandbox(config)
        mock_client = MagicMock()
        sandbox._client = mock_client

        with sandbox:
            # We do NOT call sandbox.run() — just enter and exit
            pass

        # After __exit__, _client should be None and close() should have been called
        assert sandbox._client is None
        mock_client.close.assert_called_once()
