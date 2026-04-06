"""Tests for Ollama prompt templates."""

from __future__ import annotations

from codevet.prompts import (
    build_critique_prompt,
    build_fix_prompt,
    build_test_generation_prompt,
)


class TestPromptTemplates:
    """Test prompt template construction and content."""

    def test_prompt_template_structure(self):
        """build_test_generation_prompt returns (str, str) tuple, both non-empty."""
        result = build_test_generation_prompt(
            code="def foo(): pass",
            file_name="foo.py",
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        system_prompt, user_prompt = result
        assert isinstance(system_prompt, str)
        assert isinstance(user_prompt, str)
        assert len(system_prompt) > 0
        assert len(user_prompt) > 0

    def test_prompt_forces_pytest_format(self):
        """Verify "pytest" appears in the system prompt."""
        system_prompt, _ = build_test_generation_prompt(
            code="def bar(): return 42",
            file_name="bar.py",
        )
        assert "pytest" in system_prompt.lower()

    def test_fix_prompt_includes_error(self):
        """build_fix_prompt with error_output="NameError" contains "NameError" in user prompt."""
        _, user_prompt = build_fix_prompt(
            code="def baz(): return x",
            test_code="def test_baz(): assert baz() == 1",
            error_output="NameError: name 'x' is not defined",
            iteration=1,
        )
        assert "NameError" in user_prompt

    def test_build_critique_prompt_structure(self):
        """build_critique_prompt returns (system, user) tuple with expected content."""
        system_prompt, user_prompt = build_critique_prompt(
            original_code="def foo(): return 1",
            fixed_code="def foo(): return 2",
            test_output="1 passed",
        )
        assert isinstance(system_prompt, str)
        assert isinstance(user_prompt, str)
        assert len(system_prompt) > 0
        assert len(user_prompt) > 0
        # System prompt should mention scoring
        assert "score" in system_prompt.lower()
        # User prompt should contain both code versions and test output
        assert "def foo(): return 1" in user_prompt
        assert "def foo(): return 2" in user_prompt
        assert "1 passed" in user_prompt
