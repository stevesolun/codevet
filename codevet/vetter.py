"""Ollama-powered test generation and execution."""

from __future__ import annotations

import ast
import logging
import re
from typing import TYPE_CHECKING, Literal

import ollama

from codevet.models import GeneratedTest, VetResult

if TYPE_CHECKING:
    from codevet.sandbox import Sandbox

logger = logging.getLogger(__name__)


class VetterError(Exception):
    """Raised when Ollama is unreachable, a model is missing, or vetting fails."""


class Vetter:
    """Generate and execute tests against user-supplied code via Ollama."""

    def __init__(self, model: str = "gemma2:9b") -> None:
        self.model = model
        self._client: ollama.Client | None = None

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    def _get_client(self) -> ollama.Client:
        """Return a lazily-initialised Ollama client.

        Raises ``VetterError`` if the Ollama daemon is not reachable.
        """
        if self._client is not None:
            return self._client
        try:
            client = ollama.Client()
            # Lightweight probe to confirm the daemon is alive.
            client.list()
            self._client = client
            return client
        except Exception as exc:
            raise VetterError(
                "Cannot connect to Ollama. Is the daemon running? "
                f"(underlying error: {exc})"
            ) from exc

    # ------------------------------------------------------------------
    # Test generation
    # ------------------------------------------------------------------

    def generate_tests(self, code: str, file_name: str = "code.py") -> list[GeneratedTest]:
        """Ask Ollama to generate pytest tests for *code*.

        Returns a list of ``GeneratedTest`` objects, each categorised as
        ``unit``, ``edge``, ``security``, or ``performance``.
        """
        from codevet.prompts import build_test_generation_prompt

        system_prompt, user_prompt = build_test_generation_prompt(
            code=code,
            file_name=file_name,
        )

        try:
            import time as _time
            call_start = _time.monotonic()
            logger.info("[vetter] Calling Ollama (%s) for test gen...", self.model)
            client = self._get_client()
            response = client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={
                    # Tuned for test generation: slightly higher temperature
                    # for edge-case diversity, but still mostly deterministic.
                    "temperature": 0.4,
                    "top_p": 0.9,
                    "top_k": 40,
                    "repeat_penalty": 1.1,
                },
            )
            logger.info(
                "[vetter] Ollama returned in %.1fs", _time.monotonic() - call_start
            )
        except VetterError:
            raise
        except ollama.ResponseError as exc:
            raise VetterError(
                f"Model '{self.model}' not found or Ollama error: {exc}"
            ) from exc
        except Exception as exc:
            raise VetterError(f"Ollama request failed: {exc}") from exc

        raw_text: str = response["message"]["content"]
        test_code = _strip_markdown_fences(raw_text)

        functions = _split_test_functions(test_code)
        if not functions:
            logger.warning("LLM response contained no parseable test functions.")
            return []

        test_cases: list[GeneratedTest] = []
        for name, body in functions:
            category = _categorise(name)
            test_cases.append(GeneratedTest(name=name, code=body, category=category))

        return test_cases

    # ------------------------------------------------------------------
    # Test execution
    # ------------------------------------------------------------------

    def run_tests(self, code: str, test_code: str, sandbox: Sandbox) -> VetResult:
        """Execute *test_code* against *code* inside *sandbox*.

        Returns a ``VetResult`` with parsed pass / fail / error counts.
        """
        sandbox_result = sandbox.run(code, test_code)
        combined_output = sandbox_result.stdout + "\n" + sandbox_result.stderr
        passed, failed, errors = parse_pytest_output(combined_output)

        # We don't have access to the original GeneratedTest list here, so
        # the caller is expected to populate ``test_cases`` separately.
        return VetResult(
            test_cases=[],
            passed=passed,
            failed=failed,
            errors=errors,
            raw_output=combined_output,
        )

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def vet(self, code: str, file_name: str, sandbox: Sandbox) -> VetResult:
        """Generate tests, combine them, run them, and return a ``VetResult``."""
        test_cases = self.generate_tests(code, file_name)
        test_code = combine_test_cases(test_cases)
        sandbox_result = sandbox.run(code, test_code)
        combined_output = sandbox_result.stdout + "\n" + sandbox_result.stderr
        passed, failed, errors = parse_pytest_output(combined_output)

        return VetResult(
            test_cases=test_cases,
            passed=passed,
            failed=failed,
            errors=errors,
            raw_output=combined_output,
        )


# ======================================================================
# Module-level helpers
# ======================================================================


def parse_pytest_output(output: str) -> tuple[int, int, int]:
    """Extract ``(passed, failed, errors)`` from pytest summary output.

    Looks for patterns such as ``3 passed``, ``1 failed``, ``2 error``
    in the summary line.  Returns ``(0, 0, 0)`` when parsing fails.
    """
    passed = failed = errors = 0

    match_passed = re.search(r"(\d+)\s+passed", output)
    match_failed = re.search(r"(\d+)\s+failed", output)
    match_errors = re.search(r"(\d+)\s+error", output)

    if match_passed:
        passed = int(match_passed.group(1))
    if match_failed:
        failed = int(match_failed.group(1))
    if match_errors:
        errors = int(match_errors.group(1))

    return passed, failed, errors


def combine_test_cases(test_cases: list[GeneratedTest]) -> str:
    """Combine *test_cases* into a single valid pytest file with imports."""
    if not test_cases:
        return ""

    # Collect unique import lines that appear in any test body.
    import_lines: list[str] = []
    body_lines: list[str] = []

    for tc in test_cases:
        for line in tc.code.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                if stripped not in import_lines:
                    import_lines.append(stripped)
            else:
                body_lines.append(line)

    # Always ensure pytest is imported.
    has_pytest = any(
        line.startswith("import pytest") or "import pytest" in line
        for line in import_lines
    )
    if not has_pytest:
        import_lines.insert(0, "import pytest")

    parts = import_lines + ["", ""] + body_lines
    return "\n".join(parts) + "\n"


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (```python ... ```) from LLM output."""
    # Handle ```python ... ``` blocks
    pattern = r"```(?:python)?\s*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return "\n\n".join(matches).strip()
    # If no fences found, return the text as-is.
    return text.strip()


def _split_test_functions(code: str) -> list[tuple[str, str]]:
    """Split *code* into ``(function_name, full_function_source)`` pairs.

    Uses :mod:`ast` when possible, falling back to a regex-based split
    when the code does not parse cleanly.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _split_test_functions_regex(code)

    lines = code.splitlines(keepends=True)
    results: list[tuple[str, str]] = []

    # Collect test function nodes sorted by line number.
    func_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    func_nodes.sort(key=lambda n: n.lineno)

    for i, node in enumerate(func_nodes):
        start = node.lineno - 1  # ast uses 1-based lines
        next_start = func_nodes[i + 1].lineno - 1 if i + 1 < len(func_nodes) else len(lines)
        end = next_start
        body = "".join(lines[start:end]).rstrip() + "\n"
        results.append((node.name, body))

    return results


def _split_test_functions_regex(code: str) -> list[tuple[str, str]]:
    """Regex fallback for splitting test functions."""
    pattern = re.compile(r"^(def (test_\w+)\(.*?\):.*?)(?=\ndef |\Z)", re.MULTILINE | re.DOTALL)
    results: list[tuple[str, str]] = []
    for match in pattern.finditer(code):
        func_body = match.group(1).rstrip() + "\n"
        func_name = match.group(2)
        results.append((func_name, func_body))
    return results


def _categorise(name: str) -> Literal["unit", "edge", "security", "performance"]:
    """Infer a test category from its function *name*."""
    lower = name.lower()
    if "security" in lower:
        return "security"
    if "perf" in lower or "performance" in lower:
        return "performance"
    if any(kw in lower for kw in ("edge", "boundary", "empty", "none", "zero")):
        return "edge"
    return "unit"
