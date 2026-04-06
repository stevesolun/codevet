"""Tests for utility functions."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from codevet.models import (
    CodevetOutput,
    ConfidenceScore,
    GeneratedTest,
    VetResult,
)
from codevet.utils import (
    format_diff,
    output_json,
    read_code_file,
    render_confidence_badge,
    render_diff,
    render_explanation,
    render_full_output,
)


class TestRichColoredDiff:
    """Test format_diff utility."""

    def test_rich_colored_diff(self):
        """Call format_diff with two different strings, verify output contains "---" and "+++"."""
        original = "def foo():\n    return 1\n"
        modified = "def foo():\n    return 2\n"
        output = format_diff(original, modified)
        assert "---" in output
        assert "+++" in output


class TestRichConfidenceBadge:
    """Test render_confidence_badge utility."""

    def test_rich_confidence_badge(self):
        """Verify render_confidence_badge doesn't crash with a valid ConfidenceScore."""
        score = ConfidenceScore(
            score=85,
            pass_rate=0.9,
            critique_score=0.7,
            explanation="Good quality code.",
        )
        console = Console(file=StringIO(), no_color=True)
        # Should not raise any exception
        render_confidence_badge(console, score)


class TestRichMarkdownExplanation:
    """Test render_explanation utility."""

    def test_rich_markdown_explanation(self):
        """Verify render_explanation doesn't crash with a markdown string."""
        markdown_text = "## Summary\n\nThis code has **no issues**.\n\n- Item 1\n- Item 2"
        console = Console(file=StringIO(), no_color=True)
        # Should not raise any exception
        render_explanation(console, markdown_text)

    def test_render_explanation_empty(self):
        """render_explanation with empty string produces no output."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        render_explanation(console, "")
        output = buf.getvalue()
        assert output == ""


class TestReadCodeFile:
    """Test read_code_file utility."""

    def test_read_valid_file(self, tmp_path):
        """read_code_file returns the file contents for a valid path."""
        f = tmp_path / "sample.py"
        f.write_text("print('hello')", encoding="utf-8")
        result = read_code_file(str(f))
        assert result == "print('hello')"

    def test_read_missing_file(self, tmp_path):
        """read_code_file raises FileNotFoundError for a missing path."""
        with pytest.raises(FileNotFoundError, match="Code file not found"):
            read_code_file(str(tmp_path / "nonexistent.py"))


class TestRenderDiff:
    """Test render_diff utility."""

    def test_render_diff_with_changes(self):
        """render_diff prints a panel containing diff lines."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        render_diff(console, "line1\n", "line2\n", "test.py")
        output = buf.getvalue()
        assert "Diff" in output

    def test_render_diff_no_changes(self):
        """render_diff prints 'No changes' when inputs are identical."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        render_diff(console, "same\n", "same\n", "test.py")
        output = buf.getvalue()
        assert "No changes" in output


class TestRenderFullOutput:
    """Test render_full_output utility."""

    def test_render_full_output_with_fix(self):
        """render_full_output renders header, diff, confidence, and explanation."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        confidence = ConfidenceScore(
            score=85,
            pass_rate=1.0,
            critique_score=0.5,
            explanation="Good fix.",
        )
        output_obj = CodevetOutput(
            file_path="test.py",
            original_code="def foo(): return 1\n",
            fixed_code="def foo(): return 2\n",
            confidence=confidence,
            vet_result=VetResult(
                test_cases=[
                    GeneratedTest(name="test_a", code="def test_a(): pass", category="unit"),
                ],
                passed=1,
                failed=0,
                errors=0,
                raw_output="1 passed",
            ),
            fix_result=None,
            model_used="test-model",
            duration_seconds=1.0,
        )
        render_full_output(console, output_obj)
        text = buf.getvalue()
        assert "test.py" in text
        assert "test-model" in text

    def test_render_full_output_no_fix(self):
        """render_full_output works when fixed_code is None (no changes)."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        confidence = ConfidenceScore(
            score=90,
            pass_rate=1.0,
            critique_score=0.7,
            explanation="All good.",
        )
        output_obj = CodevetOutput(
            file_path="clean.py",
            original_code="def foo(): return 1\n",
            fixed_code=None,
            confidence=confidence,
            vet_result=VetResult(
                test_cases=[],
                passed=0,
                failed=0,
                errors=0,
                raw_output="",
            ),
            fix_result=None,
            model_used="test-model",
            duration_seconds=0.5,
        )
        render_full_output(console, output_obj)
        text = buf.getvalue()
        assert "clean.py" in text


class TestOutputJson:
    """Test output_json serialization."""

    def test_output_json_valid(self):
        """output_json returns valid JSON string."""
        import json

        confidence = ConfidenceScore(
            score=70,
            pass_rate=0.8,
            critique_score=0.5,
            explanation="Decent.",
        )
        output_obj = CodevetOutput(
            file_path="test.py",
            original_code="pass",
            fixed_code="pass",
            confidence=confidence,
            vet_result=VetResult(
                test_cases=[],
                passed=0,
                failed=0,
                errors=0,
                raw_output="",
            ),
            fix_result=None,
            model_used="test-model",
            duration_seconds=0.1,
        )
        result = output_json(output_obj)
        parsed = json.loads(result)
        assert parsed["file_path"] == "test.py"
        assert parsed["model_used"] == "test-model"
