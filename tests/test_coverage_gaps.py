"""Tests targeting every uncovered line for 100% coverage.

Each test is named after the module and line(s) it covers.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from rich.console import Console

from codevet.models import (
    ConfidenceScore,
    GeneratedTest,
    SandboxConfig,
    SandboxResult,
    VetResult,
)

# ======================================================================
# cli.py — missing lines: 70-74, 141-143, 163-181, 185
# ======================================================================


class TestCliNoInput:
    """cli.py lines 70-74: no input provided error path."""

    def test_fix_no_input_exits_with_error(self) -> None:
        """When no file, no diff, and no stdin, exit code should be 1."""
        from typer.testing import CliRunner

        from codevet.cli import app

        runner = CliRunner()
        # Patch read_from_stdin to return empty (simulates no pipe)
        with patch("codevet.cli.read_from_stdin", return_value=""):
            result = runner.invoke(app, ["fix", "--skip-preflight"])

        assert result.exit_code == 1
        assert "No input provided" in result.output


class TestCliExceptionHandler:
    """cli.py lines 141-143: exception inside pipeline raises typer.Exit(1)."""

    def test_fix_exception_exits_with_error(self, tmp_path: Path) -> None:
        """When Vetter raises, the CLI catches it and exits with code 1."""
        from typer.testing import CliRunner

        from codevet.cli import app

        code_file = tmp_path / "bad.py"
        code_file.write_text("def foo(): pass")

        runner = CliRunner()
        with (
            patch("codevet.cli.Vetter", side_effect=RuntimeError("boom")),
            patch("codevet.cli.Sandbox") as mock_sandbox_cls,
        ):
            mock_sandbox_cls.return_value.__enter__ = MagicMock(
                return_value=MagicMock()
            )
            mock_sandbox_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(app, ["fix", str(code_file), "--skip-preflight"])

        assert result.exit_code == 1
        assert "boom" in result.output


class TestCliGetCritique:
    """cli.py lines 163-181: _get_critique function."""

    def test_get_critique_success(self) -> None:
        """_get_critique returns the LLM response string on success."""
        from codevet.cli import _get_critique

        mock_vetter = MagicMock()
        mock_vetter.model = "test-model"
        mock_client = MagicMock()
        mock_client.chat.return_value = {
            "message": {"content": '{"score": 80, "reasoning": "good"}'}
        }
        mock_vetter._get_client.return_value = mock_client

        result = _get_critique(mock_vetter, "orig", "fixed", "test output")
        assert '"score": 80' in result

    def test_get_critique_exception_returns_fallback(self) -> None:
        """_get_critique returns fallback JSON when exception occurs."""
        from codevet.cli import _get_critique

        mock_vetter = MagicMock()
        mock_vetter.model = "test-model"
        mock_vetter._get_client.side_effect = RuntimeError("no ollama")

        result = _get_critique(mock_vetter, "orig", "fixed", "test output")
        assert '"score": 50' in result
        assert "Critique unavailable" in result


class TestCliMainBlock:
    """cli.py line 185: if __name__ == '__main__' block."""

    def test_main_block_invokes_app(self) -> None:
        """Running cli module as __main__ exercises the if-block."""
        import runpy

        # runpy re-executes the module with __name__ == "__main__", which
        # triggers `app()`. Typer's app() will see argv and raise SystemExit.
        with pytest.raises(SystemExit):
            runpy.run_module("codevet.cli", run_name="__main__")


# ======================================================================
# fixer.py — missing lines: 45-51, 108-115
# ======================================================================


class TestFixerClientError:
    """fixer.py lines 45-51: _get_client error and success paths."""

    def test_get_client_raises_fixer_error(self) -> None:
        """When ollama.Client() fails, FixerError is raised."""
        from codevet.fixer import Fixer, FixerError

        fixer = Fixer(model="test-model")
        with patch("codevet.fixer.ollama.Client", side_effect=ConnectionError("refused")):
            with pytest.raises(FixerError, match="Cannot connect to Ollama"):
                fixer._get_client()

    def test_get_client_success_path(self) -> None:
        """When ollama.Client() succeeds, client is cached and returned."""
        from codevet.fixer import Fixer

        fixer = Fixer(model="test-model")
        mock_client = MagicMock()
        with patch("codevet.fixer.ollama.Client", return_value=mock_client):
            result = fixer._get_client()
        assert result is mock_client
        mock_client.list.assert_called_once()
        # Second call should return cached client
        result2 = fixer._get_client()
        assert result2 is mock_client


class TestFixerErrorReRaise:
    """fixer.py line 109: FixerError re-raise during fix loop."""

    def test_fix_reraises_fixer_error_from_get_client(self) -> None:
        """FixerError from _get_client during fix loop is re-raised directly."""
        from codevet.fixer import Fixer, FixerError

        fixer = Fixer(model="test-model", max_iterations=1)
        # Don't set _client so _get_client is called during fix
        with patch.object(
            fixer, "_get_client", side_effect=FixerError("daemon died")
        ):
            vet_result = VetResult(
                test_cases=[
                    GeneratedTest(
                        name="test_a", code="def test_a(): pass", category="unit"
                    )
                ],
                passed=0,
                failed=1,
                errors=0,
                raw_output="1 failed",
            )
            with pytest.raises(FixerError, match="daemon died"):
                fixer.fix("def foo(): pass", vet_result, MagicMock(), "test.py")


class TestFixerOllamaErrors:
    """fixer.py lines 108-115: ResponseError and generic Exception paths."""

    def test_fix_response_error(self) -> None:
        """ollama.ResponseError during fix raises FixerError."""
        import ollama as ollama_mod

        from codevet.fixer import Fixer, FixerError

        fixer = Fixer(model="bad-model", max_iterations=1)
        mock_client = MagicMock()
        mock_client.chat.side_effect = ollama_mod.ResponseError("model not found")
        fixer._client = mock_client

        vet_result = VetResult(
            test_cases=[
                GeneratedTest(name="test_a", code="def test_a(): pass", category="unit")
            ],
            passed=0,
            failed=1,
            errors=0,
            raw_output="1 failed",
        )

        with pytest.raises(FixerError, match="not found"):
            fixer.fix("def foo(): pass", vet_result, MagicMock(), "test.py")

    def test_fix_generic_exception(self) -> None:
        """Generic exception during fix raises FixerError."""
        from codevet.fixer import Fixer, FixerError

        fixer = Fixer(model="test-model", max_iterations=1)
        mock_client = MagicMock()
        mock_client.chat.side_effect = OSError("network down")
        fixer._client = mock_client

        vet_result = VetResult(
            test_cases=[
                GeneratedTest(name="test_a", code="def test_a(): pass", category="unit")
            ],
            passed=0,
            failed=1,
            errors=0,
            raw_output="1 failed",
        )

        with pytest.raises(FixerError, match="Ollama request failed"):
            fixer.fix("def foo(): pass", vet_result, MagicMock(), "test.py")


# ======================================================================
# models.py — missing lines: 105, 107, 119, 121
# ======================================================================


class TestModelsValidateRates:
    """models.py lines 105, 107: pass_rate and critique_score out of range."""

    def test_pass_rate_out_of_range(self) -> None:
        """pass_rate > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError, match="pass_rate"):
            ConfidenceScore(
                score=50,
                pass_rate=1.5,
                critique_score=0.5,
                explanation="test",
            )

    def test_critique_score_out_of_range(self) -> None:
        """critique_score > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError, match="critique_score"):
            ConfidenceScore(
                score=50,
                pass_rate=0.5,
                critique_score=1.5,
                explanation="test",
            )


class TestModelsGradeBranches:
    """models.py lines 119, 121: grade D and grade computation branches."""

    def test_grade_B(self) -> None:
        """Score 85 yields grade B."""
        cs = ConfidenceScore(
            score=85, pass_rate=0.85, critique_score=0.85, explanation="test"
        )
        assert cs.grade == "B"

    def test_grade_C(self) -> None:
        """Score 75 yields grade C."""
        cs = ConfidenceScore(
            score=75, pass_rate=0.75, critique_score=0.75, explanation="test"
        )
        assert cs.grade == "C"

    def test_grade_D(self) -> None:
        """Score 65 yields grade D."""
        cs = ConfidenceScore(
            score=65, pass_rate=0.65, critique_score=0.65, explanation="test"
        )
        assert cs.grade == "D"

    def test_grade_F_below_60(self) -> None:
        """Score 55 yields grade F."""
        cs = ConfidenceScore(
            score=55, pass_rate=0.55, critique_score=0.55, explanation="test"
        )
        assert cs.grade == "F"


# ======================================================================
# prompts.py — missing lines: 174-181 (already covered by test_prompts.py
# test_build_critique_prompt_structure, but let's verify coverage)
# ======================================================================


class TestBuildCritiquePromptCoverage:
    """prompts.py lines 174-181: build_critique_prompt body."""

    def test_build_critique_prompt_content(self) -> None:
        """Verify build_critique_prompt includes all code sections."""
        from codevet.prompts import build_critique_prompt

        system, user = build_critique_prompt(
            original_code="def f(): return 1",
            fixed_code="def f(): return 2",
            test_output="1 passed",
        )
        assert "ORIGINAL CODE" in user
        assert "FIXED CODE" in user
        assert "TEST OUTPUT" in user
        assert "def f(): return 1" in user
        assert "def f(): return 2" in user
        assert "1 passed" in user
        assert "score" in system.lower()


# ======================================================================
# sandbox.py — missing lines: 46, 68-72, 101, 172
# ======================================================================


class TestSandboxCachedClient:
    """sandbox.py line 46: cached client return."""

    def test_get_client_returns_cached(self) -> None:
        """Second call to _get_client returns the cached client."""
        from codevet.sandbox import Sandbox

        config = SandboxConfig(project_dir=Path("."))
        sandbox = Sandbox(config)
        mock_client = MagicMock()
        sandbox._client = mock_client

        result = sandbox._get_client()
        assert result is mock_client


class TestSandboxImagePull:
    """sandbox.py lines 68-72: image not found, pull succeeds or fails."""

    def test_ensure_image_pulls_when_not_found(self) -> None:
        """When image is missing locally, pull is attempted."""
        from docker.errors import ImageNotFound

        from codevet.sandbox import Sandbox

        config = SandboxConfig(project_dir=Path("."))
        sandbox = Sandbox(config)
        mock_client = MagicMock()
        mock_client.images.get.side_effect = ImageNotFound("not found")
        mock_client.images.pull.return_value = MagicMock()

        sandbox._ensure_image(mock_client)
        mock_client.images.pull.assert_called_once_with(config.image)

    def test_ensure_image_pull_fails(self) -> None:
        """When pull fails, DockerSandboxError is raised."""
        from docker.errors import DockerException, ImageNotFound

        from codevet.sandbox import DockerSandboxError, Sandbox

        config = SandboxConfig(project_dir=Path("."))
        sandbox = Sandbox(config)
        mock_client = MagicMock()
        mock_client.images.get.side_effect = ImageNotFound("not found")
        mock_client.images.pull.side_effect = DockerException("pull failed")

        with pytest.raises(DockerSandboxError, match="Failed to pull image"):
            sandbox._ensure_image(mock_client)


class TestSandboxRunCodeOnly:
    """sandbox.py line 101: run with test_code=None."""

    def test_run_without_test_code(self) -> None:
        """When test_code is None, command is 'python solution.py'."""
        from codevet.sandbox import Sandbox

        config = SandboxConfig(project_dir=Path("."))
        sandbox = Sandbox(config)

        mock_client = MagicMock()
        sandbox._client = mock_client
        mock_client.images.get.return_value = MagicMock()

        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.return_value = b"output"
        mock_client.containers.run.return_value = container

        result = sandbox.run("print('hello')")

        call_kwargs = mock_client.containers.run.call_args
        _, kwargs = call_kwargs
        assert kwargs["command"] == "python solution.py"
        assert result.exit_code == 0


class TestSandboxNonTimeoutError:
    """sandbox.py line 172: non-timeout container error."""

    def test_non_timeout_container_error(self) -> None:
        """Container exception that is NOT a timeout raises DockerSandboxError."""
        from codevet.sandbox import DockerSandboxError, Sandbox

        config = SandboxConfig(project_dir=Path("."))
        sandbox = Sandbox(config)

        mock_client = MagicMock()
        sandbox._client = mock_client
        mock_client.images.get.return_value = MagicMock()

        container = MagicMock()
        container.wait.side_effect = RuntimeError("unexpected docker error")
        mock_client.containers.run.return_value = container

        with pytest.raises(DockerSandboxError, match="Container execution failed"):
            sandbox.run("print('hello')", "def test_x(): pass")


# ======================================================================
# scorer.py — missing lines: 40-41, 58-59, 65, 68-69, 73-75
# ======================================================================


class TestScorerEdgeCases:
    """Cover all scorer parse_critique_response edge cases."""

    def test_empty_critique_response(self) -> None:
        """scorer.py lines 40-41: empty string returns fallback."""
        from codevet.scorer import parse_critique_response

        score, reasoning = parse_critique_response("")
        assert score == 0.5
        assert "could not be parsed" in reasoning

    def test_whitespace_only_critique_response(self) -> None:
        """scorer.py lines 40-41: whitespace-only string returns fallback."""
        from codevet.scorer import parse_critique_response

        score, reasoning = parse_critique_response("   ")
        assert score == 0.5

    def test_non_dict_json_response(self) -> None:
        """scorer.py lines 58-59: JSON array returns fallback."""
        from codevet.scorer import parse_critique_response

        score, reasoning = parse_critique_response("[1, 2, 3]")
        assert score == 0.5
        assert "could not be parsed" in reasoning

    def test_non_string_reasoning(self) -> None:
        """scorer.py line 65: numeric reasoning is converted to string."""
        from codevet.scorer import parse_critique_response

        score, reasoning = parse_critique_response('{"score": 80, "reasoning": 42}')
        assert score == 0.8
        assert reasoning == "42"

    def test_missing_score_key(self) -> None:
        """scorer.py lines 68-69: JSON without 'score' key returns fallback."""
        from codevet.scorer import parse_critique_response

        score, reasoning = parse_critique_response('{"reasoning": "no score here"}')
        assert score == 0.5

    def test_missing_score_with_empty_reasoning(self) -> None:
        """scorer.py lines 68-69: missing score, empty reasoning uses fallback reasoning."""
        from codevet.scorer import parse_critique_response

        score, reasoning = parse_critique_response('{"other": "data"}')
        assert score == 0.5
        assert "could not be parsed" in reasoning

    def test_non_integer_score(self) -> None:
        """scorer.py lines 73-75: non-integer score returns fallback."""
        from codevet.scorer import parse_critique_response

        score, reasoning = parse_critique_response(
            '{"score": "not_a_number", "reasoning": "bad"}'
        )
        assert score == 0.5

    def test_non_integer_score_with_empty_reasoning(self) -> None:
        """scorer.py lines 73-75: non-int score, empty reasoning uses fallback."""
        from codevet.scorer import parse_critique_response

        score, reasoning = parse_critique_response('{"score": "abc"}')
        assert score == 0.5
        assert "could not be parsed" in reasoning


# ======================================================================
# utils.py — missing lines: 33, 44-46, 85-86, 99, 135
# ======================================================================


class TestUtilsFileNotFound:
    """utils.py line 33: FileNotFoundError for non-existent file."""

    def test_read_code_file_not_found(self) -> None:
        """read_code_file raises FileNotFoundError for missing file."""
        from codevet.utils import read_code_file

        with pytest.raises(FileNotFoundError, match="Code file not found"):
            read_code_file("/nonexistent/path/to/file.py")


class TestUtilsStdinRead:
    """utils.py lines 44-46: read_from_stdin."""

    def test_read_from_stdin_tty(self) -> None:
        """When stdin is a TTY, returns empty string."""
        from codevet.utils import read_from_stdin

        with patch.object(sys.stdin, "isatty", return_value=True):
            result = read_from_stdin()
        assert result == ""

    def test_read_from_stdin_pipe(self) -> None:
        """When stdin is piped, returns the content."""
        from codevet.utils import read_from_stdin

        with patch.object(sys, "stdin", new=StringIO("piped content")):
            result = read_from_stdin()
        assert result == "piped content"


class TestUtilsRenderDiffNoDiff:
    """utils.py lines 85-86: render_diff with identical texts."""

    def test_render_diff_no_changes(self) -> None:
        """Identical texts produce 'No changes.' output."""
        from codevet.utils import render_diff

        console = Console(file=StringIO(), no_color=True)
        render_diff(console, "same\n", "same\n", "test.py")
        output = console.file.getvalue()
        assert "No changes" in output


class TestUtilsRenderDiffPlainLine:
    """utils.py line 99: plain context line in diff."""

    def test_render_diff_context_lines(self) -> None:
        """Diff with context lines (not +/-/@@) renders without error."""
        from codevet.utils import render_diff

        original = "line1\nline2\nline3\n"
        fixed = "line1\nchanged\nline3\n"
        console = Console(file=StringIO(), no_color=True)
        render_diff(console, original, fixed, "test.py")
        output = console.file.getvalue()
        # Should contain the diff output
        assert "Diff: test.py" in output


class TestUtilsRenderExplanationEmpty:
    """utils.py line 135: render_explanation with empty string."""

    def test_render_explanation_empty_returns_early(self) -> None:
        """Empty explanation produces no output."""
        from codevet.utils import render_explanation

        console = Console(file=StringIO(), no_color=True)
        render_explanation(console, "")
        output = console.file.getvalue()
        assert output.strip() == ""


# ======================================================================
# vetter.py — missing lines: 81, 86-87, 94-95, 113-119, 178, 216,
#             229-230, 255-261
# ======================================================================


class TestVetterReRaiseVetterError:
    """vetter.py line 81: VetterError re-raise in generate_tests."""

    def test_generate_tests_reraises_vetter_error(self) -> None:
        """VetterError from _get_client is re-raised unchanged."""
        from codevet.vetter import Vetter, VetterError

        vetter = Vetter(model="test-model")
        with patch.object(
            vetter, "_get_client", side_effect=VetterError("no daemon")
        ), pytest.raises(VetterError, match="no daemon"):
            vetter.generate_tests("def foo(): pass")


class TestVetterGenericException:
    """vetter.py lines 86-87: generic exception in generate_tests."""

    def test_generate_tests_generic_exception(self) -> None:
        """Generic exception wraps into VetterError."""
        from codevet.vetter import Vetter, VetterError

        vetter = Vetter(model="test-model")
        mock_client = MagicMock()
        mock_client.chat.side_effect = OSError("network failure")
        vetter._client = mock_client

        with pytest.raises(VetterError, match="Ollama request failed"):
            vetter.generate_tests("def foo(): pass")


class TestVetterNoTestFunctions:
    """vetter.py lines 94-95: LLM response with no test functions."""

    def test_generate_tests_no_functions(self) -> None:
        """Response with no test_ functions returns empty list."""
        from codevet.vetter import Vetter

        vetter = Vetter(model="test-model")
        mock_client = MagicMock()
        mock_client.chat.return_value = {
            "message": {"content": "# No test functions here\nx = 42\n"}
        }
        vetter._client = mock_client

        result = vetter.generate_tests("def foo(): pass")
        assert result == []


class TestVetterRunTests:
    """vetter.py lines 113-119: run_tests method."""

    def test_run_tests_returns_vet_result(self) -> None:
        """run_tests executes code+tests in sandbox and returns VetResult."""
        from codevet.vetter import Vetter

        vetter = Vetter(model="test-model")
        mock_sandbox = MagicMock()
        mock_sandbox.run.return_value = SandboxResult(
            exit_code=0,
            stdout="3 passed",
            stderr="",
            timed_out=False,
            duration_seconds=1.0,
        )

        result = vetter.run_tests(
            code="def foo(): pass",
            test_code="def test_foo(): assert True",
            sandbox=mock_sandbox,
        )

        assert isinstance(result, VetResult)
        assert result.passed == 3
        assert result.failed == 0
        assert result.errors == 0
        assert result.test_cases == []


class TestVetterCombineNoPytest:
    """vetter.py line 178: combine_test_cases inserts pytest import."""

    def test_combine_test_cases_adds_pytest_import(self) -> None:
        """When no test body has 'import pytest', it's added automatically."""
        from codevet.vetter import combine_test_cases

        tc = GeneratedTest(
            name="test_a",
            code="def test_a():\n    assert True\n",
            category="unit",
        )
        combined = combine_test_cases([tc])
        assert "import pytest" in combined


class TestVetterStripMarkdownFences:
    """vetter.py lines 216, 218: _strip_markdown_fences with and without fences."""

    def test_strip_markdown_fences_no_fences(self) -> None:
        """Text without fences is returned stripped."""
        from codevet.vetter import _strip_markdown_fences

        result = _strip_markdown_fences("  def test_a(): pass  ")
        assert result == "def test_a(): pass"

    def test_strip_markdown_fences_with_fences(self) -> None:
        """Text with ```python fences extracts the code inside."""
        from codevet.vetter import _strip_markdown_fences

        text = (
            "Here is some code:\n"
            "```python\n"
            "def test_a():\n"
            "    assert True\n"
            "```\n"
            "And more:\n"
            "```python\n"
            "def test_b():\n"
            "    assert False\n"
            "```\n"
        )
        result = _strip_markdown_fences(text)
        assert "def test_a():" in result
        assert "def test_b():" in result


class TestVetterRegexFallback:
    """vetter.py lines 229-230, 255-261: regex fallback for split."""

    def test_split_test_functions_syntax_error_uses_regex(self) -> None:
        """Code with syntax error falls back to regex splitting."""
        from codevet.vetter import _split_test_functions

        # This code has a syntax error but contains test_-prefixed defs
        code = (
            "def test_alpha():\n"
            "    assert True\n"
            "\n"
            "def test_beta():\n"
            "    assert False\n"
            "\n"
            "def broken(:\n"  # syntax error
            "    pass\n"
        )
        result = _split_test_functions(code)
        names = [name for name, _ in result]
        assert "test_alpha" in names
        assert "test_beta" in names

    def test_split_test_functions_regex_extracts_correctly(self) -> None:
        """_split_test_functions_regex finds test functions in unparseable code."""
        from codevet.vetter import _split_test_functions_regex

        code = (
            "def test_one(x):\n"
            "    return x\n"
            "\n"
            "def test_two():\n"
            "    pass\n"
        )
        result = _split_test_functions_regex(code)
        assert len(result) == 2
        assert result[0][0] == "test_one"
        assert result[1][0] == "test_two"
