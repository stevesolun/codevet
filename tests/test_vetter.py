"""Tests for Ollama vetter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codevet.models import GeneratedTest, VetResult
from codevet.vetter import (
    Vetter,
    VetterError,
    _split_test_functions,
    _split_test_functions_regex,
    combine_test_cases,
    parse_pytest_output,
)


class TestGenerateTests:
    """Test the Vetter.generate_tests method with mocked Ollama."""

    @pytest.fixture()
    def mock_ollama_response(self) -> dict:
        """Ollama response containing 10 test functions across categories."""
        code = "\n".join(
            [
                "import pytest",
                "",
                "def test_unit_add(): assert 1 + 1 == 2",
                "def test_unit_subtract(): assert 2 - 1 == 1",
                "def test_unit_multiply(): assert 2 * 3 == 6",
                "def test_unit_divide(): assert 6 / 2 == 3",
                "def test_edge_empty(): assert [] == []",
                "def test_edge_none(): assert None is None",
                "def test_edge_zero(): assert 0 == 0",
                "def test_security_injection(): assert True",
                "def test_performance_speed(): assert True",
                "def test_boundary_max(): assert True",
            ]
        )
        return {"message": {"content": code}}

    @pytest.fixture()
    def vetter_with_mock(self, mock_ollama_response: dict) -> tuple[Vetter, MagicMock]:
        """Vetter with a mocked Ollama client."""
        vetter = Vetter(model="test-model")
        mock_client = MagicMock()
        mock_client.chat.return_value = mock_ollama_response
        mock_client.list.return_value = {}
        vetter._client = mock_client
        return vetter, mock_client

    def test_generates_pytest_code(self, vetter_with_mock: tuple[Vetter, MagicMock]):
        """Mock Ollama response with valid pytest code, verify GeneratedTest objects."""
        vetter, _ = vetter_with_mock
        cases = vetter.generate_tests("def foo(): pass")
        assert len(cases) > 0
        assert all(isinstance(tc, GeneratedTest) for tc in cases)

    def test_generates_edge_cases(self, vetter_with_mock: tuple[Vetter, MagicMock]):
        """Verify at least 8 test cases generated (mock response with 10 test functions)."""
        vetter, _ = vetter_with_mock
        cases = vetter.generate_tests("def foo(): pass")
        assert len(cases) >= 8

    def test_generates_security_edges(self, vetter_with_mock: tuple[Vetter, MagicMock]):
        """Verify at least 1 test has category="security"."""
        vetter, _ = vetter_with_mock
        cases = vetter.generate_tests("def foo(): pass")
        security_cases = [tc for tc in cases if tc.category == "security"]
        assert len(security_cases) >= 1

    def test_generates_performance_edges(self, vetter_with_mock: tuple[Vetter, MagicMock]):
        """Verify at least 1 test has category="performance"."""
        vetter, _ = vetter_with_mock
        cases = vetter.generate_tests("def foo(): pass")
        perf_cases = [tc for tc in cases if tc.category == "performance"]
        assert len(perf_cases) >= 1


class TestParsePytestOutput:
    """Test parse_pytest_output helper."""

    def test_parses_pytest_output(self):
        """Parse sample pytest output "3 passed, 2 failed, 1 error"."""
        output = "===== 3 passed, 2 failed, 1 error in 0.42s ====="
        passed, failed, errors = parse_pytest_output(output)
        assert passed == 3
        assert failed == 2
        assert errors == 1


class TestOllamaErrors:
    """Test error handling for Ollama connection issues."""

    def test_handles_ollama_not_running(self):
        """Mock ollama.Client raising ConnectionError, verify VetterError."""
        vetter = Vetter(model="test-model")

        with patch("codevet.vetter.ollama.Client") as mock_cls:
            mock_cls.side_effect = ConnectionError("Connection refused")
            with pytest.raises(VetterError, match="Cannot connect to Ollama"):
                vetter._get_client()

    def test_handles_model_not_found(self):
        """Mock ollama.chat raising ResponseError with 'model not found'."""
        import ollama

        vetter = Vetter(model="nonexistent-model")
        mock_client = MagicMock()
        mock_client.list.return_value = {}
        mock_client.chat.side_effect = ollama.ResponseError("model not found")
        vetter._client = mock_client

        with pytest.raises(VetterError, match="not found"):
            vetter.generate_tests("def foo(): pass")


class TestRunTests:
    """Test the Vetter.run_tests method."""

    def test_run_tests_returns_vet_result(self):
        """run_tests parses sandbox output and returns a VetResult."""
        vetter = Vetter(model="test-model")

        sandbox = MagicMock()
        sandbox_result = MagicMock()
        sandbox_result.stdout = "3 passed, 1 failed"
        sandbox_result.stderr = ""
        sandbox.run.return_value = sandbox_result

        result = vetter.run_tests("def foo(): pass", "def test_foo(): pass", sandbox)

        assert isinstance(result, VetResult)
        assert result.passed == 3
        assert result.failed == 1
        assert result.errors == 0
        assert result.test_cases == []

    def test_run_tests_captures_stderr(self):
        """run_tests includes stderr in raw_output."""
        vetter = Vetter(model="test-model")

        sandbox = MagicMock()
        sandbox_result = MagicMock()
        sandbox_result.stdout = "0 passed, 2 failed"
        sandbox_result.stderr = "ERROR: something broke"
        sandbox.run.return_value = sandbox_result

        result = vetter.run_tests("def foo(): pass", "def test_foo(): pass", sandbox)

        assert "ERROR: something broke" in result.raw_output


class TestSplitTestFunctionsRegex:
    """Test _split_test_functions_regex fallback for unparseable code."""

    def test_regex_fallback_splits_functions(self):
        """_split_test_functions_regex splits test functions from invalid Python."""
        code = (
            "def test_one():\n    assert True\n\n"
            "def test_two():\n    assert False\n"
        )
        results = _split_test_functions_regex(code)
        assert len(results) == 2
        assert results[0][0] == "test_one"
        assert results[1][0] == "test_two"

    def test_split_test_functions_falls_back_on_syntax_error(self):
        """_split_test_functions falls back to regex when AST parse fails."""
        # Intentionally broken syntax but with recognizable test defs
        code = (
            "def test_alpha():\n    assert 1 ==\n\n"
            "def test_beta():\n    pass\n"
        )
        results = _split_test_functions(code)
        # Should still find test_beta via regex fallback
        names = [name for name, _ in results]
        assert "test_beta" in names


class TestCombineTestCases:
    """Test combine_test_cases helper."""

    def test_combine_empty_list(self):
        """combine_test_cases with empty list returns empty string."""
        assert combine_test_cases([]) == ""

    def test_combine_ensures_pytest_import(self):
        """combine_test_cases adds 'import pytest' if not present."""
        cases = [
            GeneratedTest(name="test_a", code="def test_a():\n    assert True\n", category="unit"),
        ]
        result = combine_test_cases(cases)
        assert "import pytest" in result


class TestVetterGenerateTestsEmptyResponse:
    """Test edge case where LLM returns no test functions."""

    def test_empty_response_returns_empty_list(self):
        """When LLM returns text with no test functions, generate_tests returns []."""
        vetter = Vetter(model="test-model")
        mock_client = MagicMock()
        mock_client.chat.return_value = {"message": {"content": "no tests here"}}
        vetter._client = mock_client

        cases = vetter.generate_tests("def foo(): pass")
        assert cases == []


class TestVetterGenericException:
    """Test generic exception handling in generate_tests."""

    def test_generic_exception_raises_vetter_error(self):
        """When client.chat raises a generic exception, VetterError is raised."""
        vetter = Vetter(model="test-model")
        mock_client = MagicMock()
        mock_client.chat.side_effect = RuntimeError("unexpected error")
        vetter._client = mock_client

        with pytest.raises(VetterError, match="Ollama request failed"):
            vetter.generate_tests("def foo(): pass")
