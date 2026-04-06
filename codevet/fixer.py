"""Auto-fix loop with difflib + AST validation."""

from __future__ import annotations

import ast
import difflib
import logging
import re
from typing import TYPE_CHECKING

import ollama

from codevet.models import FixAttempt, FixResult, VetResult
from codevet.vetter import combine_test_cases

if TYPE_CHECKING:
    from codevet.sandbox import Sandbox

logger = logging.getLogger(__name__)


class FixerError(Exception):
    """Raised when the auto-fix loop encounters an unrecoverable error."""


class Fixer:
    """Iteratively fix code until all generated tests pass."""

    def __init__(self, model: str = "gemma2:9b", max_iterations: int = 3) -> None:
        self.model = model
        self.max_iterations = min(max_iterations, 3)
        self._client: ollama.Client | None = None

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    def _get_client(self) -> ollama.Client:
        """Return a lazily-initialised Ollama client.

        Raises ``FixerError`` if the Ollama daemon is not reachable.
        """
        if self._client is not None:
            return self._client
        try:
            client = ollama.Client()
            client.list()
            self._client = client
            return client
        except Exception as exc:
            raise FixerError(
                "Cannot connect to Ollama. Is the daemon running? "
                f"(underlying error: {exc})"
            ) from exc

    # ------------------------------------------------------------------
    # Main fix loop
    # ------------------------------------------------------------------

    def fix(
        self,
        code: str,
        vet_result: VetResult,
        sandbox: Sandbox,
        file_name: str = "code.py",
    ) -> FixResult:
        """Iteratively prompt Ollama to fix *code* until tests pass.

        Returns a ``FixResult`` capturing every attempt, whether the fix
        succeeded, and how many iterations were consumed.
        """
        from codevet.prompts import build_fix_prompt
        from codevet.vetter import parse_pytest_output

        # Early exit when there is nothing to fix.
        if vet_result.failed == 0 and vet_result.errors == 0:
            return FixResult(
                original_code=code,
                fixed_code=code,
                attempts=[],
                success=True,
                iterations_used=0,
            )

        test_code = combine_test_cases(vet_result.test_cases)
        current_code = code
        current_output = vet_result.raw_output
        attempts: list[FixAttempt] = []

        for iteration in range(1, self.max_iterations + 1):
            logger.info(
                "[fixer] Iteration %d/%d starting",
                iteration,
                self.max_iterations,
            )
            # Build prompt for this iteration.
            system_prompt, user_prompt = build_fix_prompt(
                code=current_code,
                test_code=test_code,
                error_output=current_output,
                iteration=iteration,
            )

            try:
                logger.info(
                    "[fixer] Calling Ollama (%s) for fix (iter %d)...",
                    self.model,
                    iteration,
                )
                import time as _time
                call_start = _time.monotonic()
                client = self._get_client()
                response = client.chat(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    options={
                        # Tuned for fix generation: low temperature for
                        # deterministic, correctness-prioritizing output.
                        "temperature": 0.2,
                        "top_p": 0.85,
                        "top_k": 40,
                        "repeat_penalty": 1.1,
                    },
                )
                logger.info(
                    "[fixer] Ollama returned in %.1fs",
                    _time.monotonic() - call_start,
                )
            except FixerError:
                raise
            except ollama.ResponseError as exc:
                raise FixerError(
                    f"Model '{self.model}' not found or Ollama error: {exc}"
                ) from exc
            except Exception as exc:
                raise FixerError(f"Ollama request failed: {exc}") from exc

            raw_text: str = response["message"]["content"]
            fixed_code = self._extract_code(raw_text)
            logger.info(
                "[fixer] Extracted %d bytes of candidate fix code",
                len(fixed_code),
            )

            # Validate syntax before running tests.
            if not self._validate_syntax(fixed_code):
                logger.warning(
                    "Iteration %d: LLM produced invalid Python syntax, skipping.",
                    iteration,
                )
                attempts.append(
                    FixAttempt(
                        iteration=iteration,
                        patch=self._generate_diff(current_code, fixed_code),
                        test_result=VetResult(
                            test_cases=vet_result.test_cases,
                            passed=0,
                            failed=0,
                            errors=1,
                            raw_output="Fix produced invalid Python syntax.",
                        ),
                        explanation="LLM response failed AST validation.",
                    )
                )
                continue

            # Run tests with the candidate fix.
            logger.info(
                "[fixer] Iter %d: running %d tests in sandbox...",
                iteration,
                len(vet_result.test_cases),
            )
            sandbox_result = sandbox.run(fixed_code, test_code)
            combined_output = sandbox_result.stdout + "\n" + sandbox_result.stderr
            passed, failed, errors = parse_pytest_output(combined_output)
            logger.info(
                "[fixer] Iter %d result: passed=%d failed=%d errors=%d",
                iteration,
                passed,
                failed,
                errors,
            )

            iter_vet = VetResult(
                test_cases=vet_result.test_cases,
                passed=passed,
                failed=failed,
                errors=errors,
                raw_output=combined_output,
            )

            attempts.append(
                FixAttempt(
                    iteration=iteration,
                    patch=self._generate_diff(current_code, fixed_code),
                    test_result=iter_vet,
                    explanation=(
                        f"Iteration {iteration}: "
                        f"{passed} passed, {failed} failed, {errors} errors."
                    ),
                )
            )

            if failed == 0 and errors == 0:
                return FixResult(
                    original_code=code,
                    fixed_code=fixed_code,
                    attempts=attempts,
                    success=True,
                    iterations_used=iteration,
                )

            # Prepare for next iteration.
            current_code = fixed_code
            current_output = combined_output

        # Exhausted all iterations without full success.
        final_code = current_code
        return FixResult(
            original_code=code,
            fixed_code=final_code,
            attempts=attempts,
            success=False,
            iterations_used=len(attempts),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_code(self, response: str) -> str:
        """Extract Python source from an Ollama response.

        Strips markdown code fences when present and validates syntax.
        """
        pattern = r"```(?:python)?\s*\n(.*?)```"
        matches = re.findall(pattern, response, re.DOTALL)
        candidate = (
            max(matches, key=len).strip() if matches else response.strip()
        )

        # Attempt AST validation; return as-is regardless so the caller
        # can decide whether to use it.
        if self._validate_syntax(candidate):
            return candidate

        return candidate

    def _validate_syntax(self, code: str) -> bool:
        """Return ``True`` if *code* parses as valid Python."""
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False

    def _generate_diff(self, original: str, fixed: str) -> str:
        """Return a unified diff between *original* and *fixed*."""
        return "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                fixed.splitlines(keepends=True),
                fromfile="original.py",
                tofile="fixed.py",
            )
        )
