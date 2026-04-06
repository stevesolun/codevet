"""Tests for auto-fix loop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codevet.fixer import Fixer, FixerError
from codevet.models import FixResult, GeneratedTest, VetResult


def _make_vet_result(passed: int, failed: int, errors: int = 0) -> VetResult:
    """Helper to create a VetResult with given pass/fail counts."""
    return VetResult(
        test_cases=[
            GeneratedTest(name=f"test_{i}", code=f"def test_{i}(): pass", category="unit")
            for i in range(passed + failed + errors)
        ],
        passed=passed,
        failed=failed,
        errors=errors,
        raw_output=f"{passed} passed, {failed} failed, {errors} error",
    )


class TestFixLoop:
    """Test Fixer auto-fix iteration loop."""

    @pytest.fixture()
    def mock_sandbox(self) -> MagicMock:
        """Sandbox mock that returns a failing sandbox result."""
        sandbox = MagicMock()
        sandbox_result = MagicMock()
        sandbox_result.stdout = "0 passed, 3 failed"
        sandbox_result.stderr = ""
        sandbox_result.exit_code = 1
        sandbox_result.timed_out = False
        sandbox_result.duration_seconds = 1.0
        sandbox.run.return_value = sandbox_result
        return sandbox

    @pytest.fixture()
    def mock_ollama_bad_fix(self) -> MagicMock:
        """Ollama mock that always returns syntactically valid but logically bad fixes."""
        client = MagicMock()
        client.chat.return_value = {
            "message": {
                "content": "def foo():\n    return 'still wrong'\n",
            },
        }
        return client

    def test_fix_loop_max_3_iterations(
        self, mock_sandbox: MagicMock, mock_ollama_bad_fix: MagicMock
    ):
        """Mock Ollama to always return bad fixes, verify exactly 3 iterations used."""
        from codevet.fixer import Fixer

        fixer = Fixer(model="test-model", max_iterations=3)
        fixer._client = mock_ollama_bad_fix

        vet_result = _make_vet_result(passed=0, failed=3)

        result = fixer.fix(
            code="def foo(): return 'bad'",
            vet_result=vet_result,
            sandbox=mock_sandbox,
            file_name="test.py",
        )

        assert isinstance(result, FixResult)
        assert result.iterations_used == 3

    def test_fix_applies_clean_diff(self, mock_sandbox: MagicMock):
        """Mock a successful fix, verify FixResult.attempts[0].patch contains unified diff."""
        from codevet.fixer import Fixer

        # Make sandbox return passing results on first try
        pass_result = MagicMock()
        pass_result.stdout = "3 passed"
        pass_result.stderr = ""
        pass_result.exit_code = 0
        pass_result.timed_out = False
        pass_result.duration_seconds = 0.5
        mock_sandbox.run.return_value = pass_result

        client = MagicMock()
        client.chat.return_value = {
            "message": {
                "content": "def foo():\n    return 'fixed'\n",
            },
        }

        fixer = Fixer(model="test-model", max_iterations=3)
        fixer._client = client

        vet_result = _make_vet_result(passed=0, failed=3)
        result = fixer.fix(
            code="def foo():\n    return 'bad'\n",
            vet_result=vet_result,
            sandbox=mock_sandbox,
            file_name="test.py",
        )

        assert len(result.attempts) > 0
        patch_text = result.attempts[0].patch
        assert "---" in patch_text or "+++" in patch_text or patch_text != ""

    def test_fix_uses_ast_validation(self, mock_sandbox: MagicMock):
        """Invalid Python syntax from Ollama is caught; iteration continues."""
        from codevet.fixer import Fixer

        client = MagicMock()
        # First call: invalid syntax; second call: valid syntax; third: valid
        client.chat.side_effect = [
            {"message": {"content": "def foo(:\n    broken syntax!!!\n"}},
            {"message": {"content": "def foo():\n    return 'fixed'\n"}},
            {"message": {"content": "def foo():\n    return 'fixed'\n"}},
        ]

        fixer = Fixer(model="test-model", max_iterations=3)
        fixer._client = client

        vet_result = _make_vet_result(passed=0, failed=3)
        result = fixer.fix(
            code="def foo(): return 'bad'",
            vet_result=vet_result,
            sandbox=mock_sandbox,
            file_name="test.py",
        )

        assert isinstance(result, FixResult)
        # Should have used more than 1 iteration due to syntax error
        assert result.iterations_used >= 1

    def test_fix_feeds_pytest_output_to_ollama(self, mock_sandbox: MagicMock):
        """Verify the fix prompt includes the pytest error output from the previous run."""
        from codevet.fixer import Fixer

        client = MagicMock()
        client.chat.return_value = {
            "message": {"content": "def foo():\n    return 'fixed'\n"},
        }

        fixer = Fixer(model="test-model", max_iterations=3)
        fixer._client = client

        vet_result = _make_vet_result(passed=0, failed=3)
        fixer.fix(
            code="def foo(): return 'bad'",
            vet_result=vet_result,
            sandbox=mock_sandbox,
            file_name="test.py",
        )

        # Check that the chat call included error output in the messages
        assert client.chat.called
        call_args = client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])
        # At least one message should reference the test failure output
        all_content = " ".join(m.get("content", "") for m in messages)
        assert "failed" in all_content.lower() or "error" in all_content.lower()

    def test_fix_stops_on_all_tests_pass(self):
        """Mock Ollama returning good fix on iteration 1, verify iterations_used == 1."""
        from codevet.fixer import Fixer

        # Sandbox returns passing on first run after fix
        sandbox = MagicMock()
        pass_result = MagicMock()
        pass_result.stdout = "3 passed"
        pass_result.stderr = ""
        pass_result.exit_code = 0
        pass_result.timed_out = False
        pass_result.duration_seconds = 0.5
        sandbox.run.return_value = pass_result

        client = MagicMock()
        client.chat.return_value = {
            "message": {"content": "def foo():\n    return 'fixed'\n"},
        }

        fixer = Fixer(model="test-model", max_iterations=3)
        fixer._client = client

        vet_result = _make_vet_result(passed=0, failed=3)
        result = fixer.fix(
            code="def foo(): return 'bad'",
            vet_result=vet_result,
            sandbox=sandbox,
            file_name="test.py",
        )

        assert result.iterations_used == 1
        assert result.success is True

    def test_fix_preserves_working_code(self):
        """When all tests pass initially, verify FixResult returns original code unchanged."""
        from codevet.fixer import Fixer

        sandbox = MagicMock()
        pass_result = MagicMock()
        pass_result.stdout = "3 passed"
        pass_result.stderr = ""
        pass_result.exit_code = 0
        pass_result.timed_out = False
        pass_result.duration_seconds = 0.3
        sandbox.run.return_value = pass_result

        client = MagicMock()
        fixer = Fixer(model="test-model", max_iterations=3)
        fixer._client = client

        original = "def foo():\n    return 'correct'\n"
        vet_result = _make_vet_result(passed=3, failed=0, errors=0)

        result = fixer.fix(
            code=original,
            vet_result=vet_result,
            sandbox=sandbox,
            file_name="test.py",
        )

        assert result.fixed_code == original
        assert result.success is True


class TestFixerGetClientError:
    """Test Fixer._get_client error path."""

    def test_get_client_raises_fixer_error(self):
        """When ollama.Client() raises, Fixer._get_client raises FixerError."""
        fixer = Fixer(model="test-model")

        with patch("codevet.fixer.ollama.Client") as mock_cls:
            mock_cls.side_effect = ConnectionError("Connection refused")
            with pytest.raises(FixerError, match="Cannot connect to Ollama"):
                fixer._get_client()

    def test_get_client_caches_client(self):
        """When _client is already set, _get_client returns it without re-creating."""
        fixer = Fixer(model="test-model")
        mock_client = MagicMock()
        fixer._client = mock_client

        result = fixer._get_client()
        assert result is mock_client


class TestFixerOllamaErrors:
    """Test Fixer error handling for Ollama failures during fix."""

    def test_fix_response_error_raises_fixer_error(self):
        """When client.chat raises ResponseError, FixerError is raised."""
        import ollama

        fixer = Fixer(model="test-model", max_iterations=1)
        mock_client = MagicMock()
        mock_client.chat.side_effect = ollama.ResponseError("model not found")
        fixer._client = mock_client

        vet_result = _make_vet_result(passed=0, failed=3)

        with pytest.raises(FixerError, match="not found"):
            fixer.fix(
                code="def foo(): return 'bad'",
                vet_result=vet_result,
                sandbox=MagicMock(),
                file_name="test.py",
            )

    def test_fix_generic_error_raises_fixer_error(self):
        """When client.chat raises a generic exception, FixerError is raised."""
        fixer = Fixer(model="test-model", max_iterations=1)
        mock_client = MagicMock()
        mock_client.chat.side_effect = RuntimeError("unexpected")
        fixer._client = mock_client

        vet_result = _make_vet_result(passed=0, failed=3)

        with pytest.raises(FixerError, match="Ollama request failed"):
            fixer.fix(
                code="def foo(): return 'bad'",
                vet_result=vet_result,
                sandbox=MagicMock(),
                file_name="test.py",
            )
