"""Tests for CLI integration."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from codevet.cli import _get_critique, app
from codevet.models import (
    ConfidenceScore,
    FixAttempt,
    FixResult,
    GeneratedTest,
    VetResult,
)

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def _mock_pipeline_components():
    """Return a dict of patches that mock the full codevet pipeline."""
    vet_result = VetResult(
        test_cases=[
            GeneratedTest(name="test_a", code="def test_a(): pass", category="unit"),
        ],
        passed=0,
        failed=1,
        errors=0,
        raw_output="0 passed, 1 failed",
    )

    fix_attempt = FixAttempt(
        iteration=1,
        patch="--- a\n+++ b\n",
        test_result=VetResult(
            test_cases=[
                GeneratedTest(name="test_a", code="def test_a(): pass", category="unit"),
            ],
            passed=1,
            failed=0,
            errors=0,
            raw_output="1 passed",
        ),
        explanation="Fixed the bug.",
    )

    fix_result = FixResult(
        original_code="def bad(): pass",
        fixed_code="def good(): pass",
        attempts=[fix_attempt],
        success=True,
        iterations_used=1,
    )

    confidence = ConfidenceScore(
        score=85,
        pass_rate=1.0,
        critique_score=0.5,
        explanation="Good fix.",
    )

    return vet_result, fix_result, confidence


class TestCliFixFile:
    """Test the 'fix' command with a file argument."""

    def test_cli_fix_file(self, tmp_path: Path):
        """Mock vetter+fixer+sandbox, invoke "fix tests/fixtures/buggy_auth.py"."""
        code_file = tmp_path / "buggy_auth.py"
        code_file.write_text("def authenticate(u, p): return f'SELECT {u}'")

        vet_result, fix_result, confidence = _mock_pipeline_components()

        with (
            patch("codevet.cli.Vetter") as mock_vetter_cls,
            patch("codevet.cli.Fixer") as mock_fixer_cls,
            patch("codevet.cli.Sandbox") as mock_sandbox_cls,
            patch("codevet.cli.score_fix", return_value=confidence),
            patch("codevet.cli._get_critique", return_value='{"score": 50}'),
        ):
            mock_vetter = MagicMock()
            mock_vetter.vet.return_value = vet_result
            mock_vetter.model = "gemma2:9b"
            mock_vetter._get_client.return_value = MagicMock()
            mock_vetter_cls.return_value = mock_vetter

            mock_fixer = MagicMock()
            mock_fixer.fix.return_value = fix_result
            mock_fixer_cls.return_value = mock_fixer

            mock_sandbox_cls.return_value.__enter__ = MagicMock()
            mock_sandbox_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(app, ["fix", str(code_file), "--skip-preflight"])

        assert result.exit_code == 0


class TestCliDiffFlag:
    """Test the --diff flag."""

    def test_cli_fix_diff(self, tmp_path: Path):
        """Mock everything, invoke with --diff flag."""
        diff_file = tmp_path / "changes.diff"
        diff_file.write_text("--- a/foo.py\n+++ b/foo.py\n-old\n+new\n")

        vet_result, fix_result, confidence = _mock_pipeline_components()

        with (
            patch("codevet.cli.Vetter") as mock_vetter_cls,
            patch("codevet.cli.Fixer") as mock_fixer_cls,
            patch("codevet.cli.Sandbox") as mock_sandbox_cls,
            patch("codevet.cli.score_fix", return_value=confidence),
            patch("codevet.cli._get_critique", return_value='{"score": 50}'),
        ):
            mock_vetter = MagicMock()
            mock_vetter.vet.return_value = vet_result
            mock_vetter.model = "gemma2:9b"
            mock_vetter._get_client.return_value = MagicMock()
            mock_vetter_cls.return_value = mock_vetter

            mock_fixer = MagicMock()
            mock_fixer.fix.return_value = fix_result
            mock_fixer_cls.return_value = mock_fixer

            mock_sandbox_cls.return_value.__enter__ = MagicMock()
            mock_sandbox_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(app, ["fix", "--diff", str(diff_file), "--skip-preflight"])

        assert result.exit_code == 0


class TestCliStdin:
    """Test stdin pipe input."""

    def test_cli_stdin_pipe(self):
        """Mock everything, pipe code via stdin."""
        vet_result, fix_result, confidence = _mock_pipeline_components()

        with (
            patch("codevet.cli.Vetter") as mock_vetter_cls,
            patch("codevet.cli.Fixer") as mock_fixer_cls,
            patch("codevet.cli.Sandbox") as mock_sandbox_cls,
            patch("codevet.cli.score_fix", return_value=confidence),
            patch("codevet.cli._get_critique", return_value='{"score": 50}'),
            patch("codevet.cli.read_from_stdin", return_value="def foo(): pass"),
        ):
            mock_vetter = MagicMock()
            mock_vetter.vet.return_value = vet_result
            mock_vetter.model = "gemma2:9b"
            mock_vetter._get_client.return_value = MagicMock()
            mock_vetter_cls.return_value = mock_vetter

            mock_fixer = MagicMock()
            mock_fixer.fix.return_value = fix_result
            mock_fixer_cls.return_value = mock_fixer

            mock_sandbox_cls.return_value.__enter__ = MagicMock()
            mock_sandbox_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(app, ["fix", "--skip-preflight"])

        assert result.exit_code == 0


class TestCliJsonOutput:
    """Test --json output flag."""

    def test_cli_json_output(self, tmp_path: Path):
        """Invoke with --json flag, verify output is valid JSON."""
        code_file = tmp_path / "test_code.py"
        code_file.write_text("def foo(): pass")

        # Build a VetResult with all tests passing (no fix needed)
        vet_result = VetResult(
            test_cases=[
                GeneratedTest(name="test_a", code="def test_a(): pass", category="unit"),
            ],
            passed=1,
            failed=0,
            errors=0,
            raw_output="1 passed",
        )
        confidence = ConfidenceScore(
            score=90,
            pass_rate=1.0,
            critique_score=0.7,
            explanation="All good.",
        )

        with (
            patch("codevet.cli.Vetter") as mock_vetter_cls,
            patch("codevet.cli.Fixer") as mock_fixer_cls,
            patch("codevet.cli.Sandbox") as mock_sandbox_cls,
            patch("codevet.cli.score_fix", return_value=confidence),
            patch("codevet.cli._get_critique", return_value='{"score": 70}'),
        ):
            mock_vetter = MagicMock()
            mock_vetter.vet.return_value = vet_result
            mock_vetter.model = "gemma2:9b"
            mock_vetter._get_client.return_value = MagicMock()
            mock_vetter_cls.return_value = mock_vetter

            mock_fixer = MagicMock()
            mock_fixer_cls.return_value = mock_fixer

            mock_sandbox_cls.return_value.__enter__ = MagicMock()
            mock_sandbox_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(app, ["fix", str(code_file), "--json", "--skip-preflight"])

        assert result.exit_code == 0
        # Verify the output is valid JSON
        output_text = result.output.strip()
        if output_text:
            parsed = json.loads(output_text)
            assert isinstance(parsed, dict)


class TestCliModelFlag:
    """Test --model flag."""

    def test_cli_model_flag(self, tmp_path: Path):
        """Invoke with --model qwen2.5-coder:7b, verify model passed to Vetter."""
        code_file = tmp_path / "test_code.py"
        code_file.write_text("def foo(): pass")

        vet_result = VetResult(
            test_cases=[
                GeneratedTest(name="test_a", code="def test_a(): pass", category="unit"),
            ],
            passed=1,
            failed=0,
            errors=0,
            raw_output="1 passed",
        )
        confidence = ConfidenceScore(
            score=90,
            pass_rate=1.0,
            critique_score=0.7,
            explanation="All good.",
        )

        with (
            patch("codevet.cli.Vetter") as mock_vetter_cls,
            patch("codevet.cli.Fixer") as mock_fixer_cls,
            patch("codevet.cli.Sandbox") as mock_sandbox_cls,
            patch("codevet.cli.score_fix", return_value=confidence),
            patch("codevet.cli._get_critique", return_value='{"score": 70}'),
        ):
            mock_vetter = MagicMock()
            mock_vetter.vet.return_value = vet_result
            mock_vetter.model = "qwen2.5-coder:7b"
            mock_vetter._get_client.return_value = MagicMock()
            mock_vetter_cls.return_value = mock_vetter

            mock_fixer = MagicMock()
            mock_fixer_cls.return_value = mock_fixer

            mock_sandbox_cls.return_value.__enter__ = MagicMock()
            mock_sandbox_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(
                app,
                [
                    "fix",
                    str(code_file),
                    "--model",
                    "qwen2.5-coder:7b",
                    "--skip-preflight",
                ],
            )

        # Verify Vetter was instantiated with the correct model
        mock_vetter_cls.assert_called_once_with(model="qwen2.5-coder:7b")
        assert result.exit_code == 0


class TestGetCritiqueFallback:
    """Test the _get_critique fallback path when the LLM call fails."""

    def test_get_critique_returns_fallback_on_error(self):
        """When vetter._get_client() raises, _get_critique returns fallback JSON."""
        mock_vetter = MagicMock()
        mock_vetter._get_client.side_effect = RuntimeError("Ollama down")
        mock_vetter.model = "test-model"

        result = _get_critique(
            vetter=mock_vetter,
            original="def foo(): return 1",
            fixed="def foo(): return 2",
            vet_result=MagicMock(),
        )

        assert '"score": 50' in result
        assert "Critique unavailable" in result

    def test_get_critique_returns_fallback_on_chat_error(self):
        """When client.chat() raises, _get_critique returns fallback JSON."""
        mock_client = MagicMock()
        mock_client.chat.side_effect = RuntimeError("Model unavailable")

        mock_vetter = MagicMock()
        mock_vetter._get_client.return_value = mock_client
        mock_vetter.model = "test-model"

        result = _get_critique(
            vetter=mock_vetter,
            original="def foo(): return 1",
            fixed="def foo(): return 2",
            vet_result=MagicMock(),
        )

        assert '"score": 50' in result
        assert "Critique unavailable" in result


class TestCliNoInput:
    """Test the CLI when no input is provided."""

    def test_cli_no_input_exits_1(self):
        """Invoke 'fix' with no file, no diff, no stdin -- should exit with code 1."""
        with patch("codevet.cli.read_from_stdin", return_value=""):
            result = runner.invoke(app, ["fix", "--skip-preflight"])

        assert result.exit_code == 1

    def test_cli_exception_exits_1(self, tmp_path: Path):
        """When pipeline raises an exception, CLI exits with code 1."""
        code_file = tmp_path / "code.py"
        code_file.write_text("def foo(): pass")

        with (
            patch("codevet.cli.Vetter") as mock_vetter_cls,
            patch("codevet.cli.Fixer"),
            patch("codevet.cli.Sandbox") as mock_sandbox_cls,
        ):
            mock_vetter = MagicMock()
            mock_vetter.vet.side_effect = RuntimeError("boom")
            mock_vetter_cls.return_value = mock_vetter

            mock_sandbox_cls.return_value.__enter__ = MagicMock()
            mock_sandbox_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(app, ["fix", str(code_file), "--skip-preflight"])

        assert result.exit_code == 1
