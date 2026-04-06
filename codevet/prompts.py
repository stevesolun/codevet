"""Ollama prompt templates for test generation and auto-fixing."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

TEST_GENERATION_SYSTEM_PROMPT: str = """\
You are a senior Python security-testing expert. Your sole task is to generate \
pytest test cases that EXPOSE bugs in the provided Python code.

CORE PRINCIPLE — CRITICAL:
A test should FAIL on buggy code and PASS on correct code.
Never write a test that validates the bug as the expected behavior.

RULES — follow every one exactly:

1. Output between 8 and 12 test functions. No fewer, no more.
2. Every test must use the ``pytest`` framework with plain ``assert`` statements.
3. Name each test following this pattern: test_<what>_<condition>_<expected>
   Example: test_add_negative_numbers_returns_negative_sum
4. Import only ``pytest`` and the module under test. Do NOT import anything \
   from the standard library unless the code under test requires it.
5. Cover ALL of the following categories (at least 2 tests per category):
   a) Unit tests — verify core happy-path behavior.
   b) Edge-case mutations — off-by-one errors, empty inputs, boundary values, \
      type coercion bugs.
   c) Security edge cases — SQL injection strings, command injection payloads, \
      XSS vectors, path traversal sequences (``../``), null bytes.
   d) Performance / resource edge cases — very large inputs, deeply nested \
      structures, potential infinite loops, resource leaks.
6. For each test, include a brief one-line docstring explaining what it checks.

SECURITY TEST PATTERNS — USE THESE TEMPLATES:

For SQL injection:
    def test_<fn>_sanitizes_sql_injection():
        malicious = "admin' OR '1'='1"
        result = <fn>(malicious, "pw")
        # Correct code uses parameterized queries.
        # Raw interpolation leaves the payload in the query string.
        assert malicious not in str(result), \\
            "SQL injection payload leaked into query"

For command injection:
    def test_<fn>_rejects_command_injection():
        payload = "benign; rm -rf /"
        with pytest.raises((ValueError, RuntimeError)):
            <fn>(payload)

For path traversal:
    def test_<fn>_blocks_path_traversal():
        with pytest.raises((ValueError, PermissionError, FileNotFoundError)):
            <fn>("../../etc/passwd")

For division by zero / empty input:
    def test_<fn>_handles_empty_input():
        with pytest.raises((ValueError, ZeroDivisionError)):
            <fn>([])

For off-by-one:
    def test_<fn>_boundary_first_index():
        lst = [10, 20, 30]
        assert <fn>(lst, 0) == 10  # Must return the FIRST element, not the second

7. Actively look for these common defects:
   - Off-by-one errors
   - Empty input handling (empty string, empty list, empty dict)
   - None / null handling
   - Type coercion bugs (int vs str, float precision)
   - Boundary values (0, -1, sys.maxsize, float('inf'))
   - SQL injection via f-strings or .format()
   - Command injection via subprocess or os.system
   - XSS via unsanitized HTML
   - Path traversal via user-controlled file paths
   - Resource leaks (unclosed files, sockets)
   - Infinite loops triggered by edge inputs

OUTPUT FORMAT — CRITICAL:
- Emit ONLY valid Python source code.
- Do NOT wrap the code in markdown fences (no ```python, no ```).
- Do NOT include any explanation, commentary, or prose.
- The first line of output must be an import statement.
"""

FIX_SYSTEM_PROMPT: str = """\
You are a senior Python developer tasked with fixing bugs.

RULES — follow every one exactly:

1. You will receive:
   a) The original Python source file.
   b) The pytest test code that was run against it.
   c) The exact pytest error output (including tracebacks).
2. Analyze the error output carefully. Identify the ROOT CAUSE of each failure.
3. Apply MINIMAL, TARGETED fixes. Change only the lines that are wrong. \
   Do NOT rewrite the entire file, rename functions, or restructure code \
   unless the bug strictly requires it.
4. After fixing, briefly state (as a Python comment at the top of the file):
   - What was wrong.
   - Why the fix works.
5. Preserve all existing function signatures, class names, and public API.
6. Do NOT add new dependencies or imports beyond what was already present, \
   unless the fix requires it.

OUTPUT FORMAT — CRITICAL:
- Emit the COMPLETE fixed Python file, top to bottom.
- Do NOT emit partial patches, diffs, or hunks.
- Do NOT wrap the code in markdown fences (no ```python, no ```).
- Do NOT include any explanation outside the file itself.
- The first non-comment line must be valid Python.
"""

CRITIQUE_SYSTEM_PROMPT: str = """\
You are a code-quality evaluator. Score the quality of a code fix on a \
0-to-100 integer scale.

EVALUATION CRITERIA (weight each roughly equally):
1. Correctness — Does the fix address the root cause? Will the tests pass?
2. Completeness — Are ALL failing tests addressed, not just some?
3. Side effects — Does the fix introduce new bugs, break other tests, \
   or change intended behavior?
4. Code quality — Is the fix clean, readable, and idiomatic Python?
5. Security — Does the fix introduce or leave open any security issues?

OUTPUT FORMAT — CRITICAL:
- Emit ONLY a single JSON object. No markdown fences, no prose before or after.
- The JSON must have exactly two keys:
  {"score": <int 0-100>, "reasoning": "<one paragraph string>"}
- ``score`` is an integer from 0 (terrible) to 100 (perfect).
- ``reasoning`` is a concise explanation (1-3 sentences) of the score.
"""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def build_test_generation_prompt(code: str, file_name: str) -> tuple[str, str]:
    """Build the (system, user) prompt pair for test generation.

    Args:
        code: The Python source code to generate tests for.
        file_name: The original file name (for display only — the sandbox
            always saves the file as ``solution.py`` to avoid shadowing
            Python's stdlib ``code`` module).

    Returns:
        A tuple of (system_prompt, user_prompt).
    """
    user_prompt = (
        f"Generate pytest tests for the following Python file.\n"
        f"Original file name: {file_name}\n"
        f"IMPORTANT: In the sandbox the file is saved as 'solution.py'. "
        f"Your tests MUST import from 'solution' like this:\n"
        f"    from solution import <function_name>\n"
        f"DO NOT use any other module name. DO NOT use the original file name.\n\n"
        f"--- SOURCE CODE ---\n{code}\n--- END SOURCE CODE ---"
    )

    return TEST_GENERATION_SYSTEM_PROMPT, user_prompt


def build_fix_prompt(
    code: str,
    test_code: str,
    error_output: str,
    iteration: int,
) -> tuple[str, str]:
    """Build the (system, user) prompt pair for auto-fixing.

    Args:
        code: The current (possibly already partially fixed) Python source.
        test_code: The pytest test code that was run.
        error_output: The raw pytest stdout/stderr output.
        iteration: Which fix attempt this is (1-indexed, max 3).

    Returns:
        A tuple of (system_prompt, user_prompt).
    """
    retry_hint = (
        "Previous attempts failed — try a different approach."
        if iteration > 1
        else "Analyze carefully before changing anything."
    )
    iteration_note = f"This is fix attempt {iteration} of 3. {retry_hint}"

    user_prompt = (
        f"{iteration_note}\n\n"
        f"--- ORIGINAL SOURCE CODE ---\n{code}\n--- END SOURCE CODE ---\n\n"
        f"--- TEST CODE ---\n{test_code}\n--- END TEST CODE ---\n\n"
        f"--- PYTEST ERROR OUTPUT ---\n{error_output}\n--- END PYTEST OUTPUT ---"
    )

    return FIX_SYSTEM_PROMPT, user_prompt


def build_critique_prompt(
    original_code: str,
    fixed_code: str,
    test_output: str,
) -> tuple[str, str]:
    """Build the (system, user) prompt pair for LLM self-critique scoring.

    Args:
        original_code: The original Python source before fixes.
        fixed_code: The Python source after fixes were applied.
        test_output: The pytest output from running tests against the fixed code.

    Returns:
        A tuple of (system_prompt, user_prompt).
    """
    user_prompt = (
        f"Score the quality of the following code fix.\n\n"
        f"--- ORIGINAL CODE ---\n{original_code}\n--- END ORIGINAL CODE ---\n\n"
        f"--- FIXED CODE ---\n{fixed_code}\n--- END FIXED CODE ---\n\n"
        f"--- TEST OUTPUT ---\n{test_output}\n--- END TEST OUTPUT ---"
    )

    return CRITIQUE_SYSTEM_PROMPT, user_prompt
