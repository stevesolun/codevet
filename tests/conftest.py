"""Shared pytest fixtures for codevet test suite."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock  # noqa: TC003 – used as constructor in fixture bodies

import pytest

from codevet.models import GeneratedTest, SandboxConfig, VetResult

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.fixture()
def sample_buggy_code() -> str:
    """Python code with a deliberate off-by-one bug in a list function."""
    return (
        "def get_last_n(items: list, n: int) -> list:\n"
        '    """Return the last n items from a list."""\n'
        "    return items[len(items) - n + 1:]\n"
    )


@pytest.fixture()
def sample_safe_code() -> str:
    """Working Python code with no bugs."""
    return (
        "def add(a: int, b: int) -> int:\n"
        '    """Return the sum of two integers."""\n'
        "    return a + b\n"
        "\n"
        "\n"
        "def factorial(n: int) -> int:\n"
        '    """Return the factorial of a non-negative integer."""\n'
        "    if n < 0:\n"
        '        raise ValueError("n must be non-negative")\n'
        "    if n <= 1:\n"
        "        return 1\n"
        "    return n * factorial(n - 1)\n"
    )


@pytest.fixture()
def sample_security_bug() -> str:
    """Python code with a SQL injection vulnerability."""
    return (
        "import sqlite3\n"
        "\n"
        "\n"
        "def get_user(db_path: str, username: str) -> dict | None:\n"
        '    """Fetch a user by username (VULNERABLE to SQL injection)."""\n'
        '    conn = sqlite3.connect(db_path)\n'
        '    cursor = conn.cursor()\n'
        '    query = f"SELECT * FROM users WHERE username = \'{username}\'"\n'
        "    cursor.execute(query)\n"
        "    row = cursor.fetchone()\n"
        "    conn.close()\n"
        "    return dict(row) if row else None\n"
    )


@pytest.fixture()
def mock_docker_client(mocker: MockerFixture) -> MagicMock:
    """Mocked docker.DockerClient."""
    client = mocker.MagicMock()
    container = mocker.MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = b"All tests passed"
    client.containers.run.return_value = container
    return client


@pytest.fixture()
def mock_ollama_client(mocker: MockerFixture) -> MagicMock:
    """Mocked ollama client."""
    client = mocker.MagicMock()
    client.chat.return_value = {
        "message": {
            "content": "def test_example():\n    assert True\n",
        },
    }
    return client


@pytest.fixture()
def sandbox_config(tmp_path: Path) -> SandboxConfig:
    """Default SandboxConfig with a tmp_path project_dir."""
    return SandboxConfig(project_dir=tmp_path)


@pytest.fixture()
def sample_test_cases() -> list[GeneratedTest]:
    """List of 3 GeneratedTest objects (one unit, one edge, one security)."""
    return [
        GeneratedTest(
            name="test_add_positive_numbers",
            code="def test_add_positive_numbers():\n    assert add(2, 3) == 5\n",
            category="unit",
        ),
        GeneratedTest(
            name="test_add_empty_input",
            code="def test_add_empty_input():\n    assert add(0, 0) == 0\n",
            category="edge",
        ),
        GeneratedTest(
            name="test_no_sql_injection",
            code=(
                "def test_no_sql_injection():\n"
                "    result = get_user(db, \"'; DROP TABLE users; --\")\n"
                "    assert result is None\n"
            ),
            category="security",
        ),
    ]


@pytest.fixture()
def sample_vet_result(sample_test_cases: list[GeneratedTest]) -> VetResult:
    """VetResult with mixed pass/fail results."""
    return VetResult(
        test_cases=sample_test_cases,
        passed=2,
        failed=1,
        errors=0,
        raw_output="2 passed, 1 failed",
    )
