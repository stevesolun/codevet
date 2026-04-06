"""E2E tests that mock at the Docker/Ollama boundary."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from codevet.models import VetResult


def _read_fixture(name: str) -> str:
    """Read a fixture file from tests/fixtures/."""
    fixture_path = Path(__file__).parent / "fixtures" / name
    return fixture_path.read_text(encoding="utf-8")


def _mock_ollama_test_generation(code: str) -> dict:
    """Return a mock Ollama response with 10 test functions."""
    test_code = "\n".join(
        [
            "import pytest",
            "",
            f"# Tests for: {code[:30]}...",
            "def test_unit_basic(): assert True",
            "def test_unit_return_type(): assert True",
            "def test_unit_input_validation(): assert True",
            "def test_edge_empty_input(): assert True",
            "def test_edge_none_input(): assert True",
            "def test_edge_boundary(): assert True",
            "def test_edge_zero(): assert True",
            "def test_security_injection(): assert True",
            "def test_performance_large_input(): assert True",
            "def test_unit_negative(): assert True",
        ]
    )
    return {"message": {"content": test_code}}


def _mock_ollama_fix_response() -> dict:
    """Return a mock Ollama response with fixed code."""
    return {
        "message": {
            "content": "def fixed_function():\n    return 'fixed'\n",
        },
    }


def _setup_full_pipeline_mocks(
    mock_docker_mod: MagicMock,
    mock_ollama_mod: MagicMock,
    passing: bool = True,
) -> None:
    """Configure Docker and Ollama mocks for a full pipeline run."""
    # Docker mock
    container = MagicMock()
    if passing:
        container.wait.return_value = {"StatusCode": 0}
        container.logs.return_value = b"10 passed in 0.5s"
    else:
        container.wait.return_value = {"StatusCode": 1}
        container.logs.return_value = b"7 passed, 3 failed in 0.5s"

    client = MagicMock()
    client.containers.run.return_value = container
    mock_docker_mod.from_env.return_value = client

    # Ollama mock
    ollama_client = MagicMock()
    ollama_client.list.return_value = {}
    ollama_client.chat.side_effect = [
        _mock_ollama_test_generation("test code"),  # Test generation
        _mock_ollama_fix_response(),  # Fix attempt (if needed)
        {"message": {"content": '{"score": 75, "reasoning": "decent"}'}},  # Critique
    ]
    mock_ollama_mod.Client.return_value = ollama_client


class TestE2EBuggyAuth:
    """E2E test with buggy auth code fixture."""

    def test_e2e_buggy_auth(self):
        """Full pipeline with buggy auth code fixture."""
        code = _read_fixture("buggy_auth.py")

        with (
            patch("codevet.sandbox.docker") as mock_docker,
            patch("codevet.vetter.ollama") as mock_ollama,
            patch("codevet.fixer.ollama") as mock_fixer_ollama,
        ):
            _setup_full_pipeline_mocks(mock_docker, mock_ollama, passing=False)

            # Set up fixer ollama mock separately
            fixer_client = MagicMock()
            fixer_client.chat.return_value = _mock_ollama_fix_response()
            mock_fixer_ollama.Client.return_value = fixer_client

            from codevet.models import SandboxConfig
            from codevet.sandbox import Sandbox
            from codevet.vetter import Vetter

            config = SandboxConfig(project_dir=Path.cwd())
            vetter = Vetter(model="test-model")

            with Sandbox(config) as sb:
                vet_result = vetter.vet(code, "buggy_auth.py", sb)

            assert isinstance(vet_result, VetResult)
            assert vet_result.total >= 0
            # Mock setup has 3 failed, 7 passed
            assert vet_result.passed == 7
            assert vet_result.failed == 3


class TestE2EBuggyEdgeCases:
    """E2E test with edge case code fixture."""

    def test_e2e_buggy_edge_cases(self):
        """Full pipeline with edge case code."""
        code = _read_fixture("buggy_edge_cases.py")

        with (
            patch("codevet.sandbox.docker") as mock_docker,
            patch("codevet.vetter.ollama") as mock_ollama,
        ):
            _setup_full_pipeline_mocks(mock_docker, mock_ollama, passing=False)

            from codevet.models import SandboxConfig
            from codevet.sandbox import Sandbox
            from codevet.vetter import Vetter

            config = SandboxConfig(project_dir=Path.cwd())
            vetter = Vetter(model="test-model")

            with Sandbox(config) as sb:
                vet_result = vetter.vet(code, "buggy_edge_cases.py", sb)

            assert isinstance(vet_result, VetResult)
            # Mock setup: passing=False means 7 passed, 3 failed
            assert vet_result.passed == 7
            assert vet_result.failed == 3


class TestE2EBuggySecurity:
    """E2E test with security vulnerability code fixture."""

    def test_e2e_buggy_security(self):
        """Full pipeline with security vuln code."""
        code = _read_fixture("buggy_security.py")

        with (
            patch("codevet.sandbox.docker") as mock_docker,
            patch("codevet.vetter.ollama") as mock_ollama,
        ):
            _setup_full_pipeline_mocks(mock_docker, mock_ollama, passing=False)

            from codevet.models import SandboxConfig
            from codevet.sandbox import Sandbox
            from codevet.vetter import Vetter

            config = SandboxConfig(project_dir=Path.cwd())
            vetter = Vetter(model="test-model")

            with Sandbox(config) as sb:
                vet_result = vetter.vet(code, "buggy_security.py", sb)

            assert isinstance(vet_result, VetResult)
            assert vet_result.passed == 7
            assert vet_result.failed == 3


class TestE2EBuggyTypes:
    """E2E test with type bug code fixture."""

    def test_e2e_buggy_types(self):
        """Full pipeline with type bug code."""
        code = _read_fixture("buggy_types.py")

        with (
            patch("codevet.sandbox.docker") as mock_docker,
            patch("codevet.vetter.ollama") as mock_ollama,
        ):
            _setup_full_pipeline_mocks(mock_docker, mock_ollama, passing=True)

            from codevet.models import SandboxConfig
            from codevet.sandbox import Sandbox
            from codevet.vetter import Vetter

            config = SandboxConfig(project_dir=Path.cwd())
            vetter = Vetter(model="test-model")

            with Sandbox(config) as sb:
                vet_result = vetter.vet(code, "buggy_types.py", sb)

            assert isinstance(vet_result, VetResult)
            # Mock setup: passing=True means 10 passed, 0 failed
            assert vet_result.passed == 10
            assert vet_result.failed == 0


class TestE2EPipelinePerformance:
    """E2E performance test."""

    def test_e2e_pipeline_under_2min(self):
        """Verify total test execution time < 120s."""
        code = _read_fixture("buggy_auth.py")
        start = time.monotonic()

        with (
            patch("codevet.sandbox.docker") as mock_docker,
            patch("codevet.vetter.ollama") as mock_ollama,
        ):
            _setup_full_pipeline_mocks(mock_docker, mock_ollama, passing=True)

            from codevet.models import SandboxConfig
            from codevet.sandbox import Sandbox
            from codevet.vetter import Vetter

            config = SandboxConfig(project_dir=Path.cwd())
            vetter = Vetter(model="test-model")

            with Sandbox(config) as sb:
                vet_result = vetter.vet(code, "buggy_auth.py", sb)

        elapsed = time.monotonic() - start
        assert elapsed < 120, f"Pipeline took {elapsed:.2f}s, exceeds 120s limit"
        assert isinstance(vet_result, VetResult)
