"""Confidence scoring for codevet fix results."""
from __future__ import annotations

import json
import logging

from codevet.models import ConfidenceScore, VetResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Component functions
# ---------------------------------------------------------------------------


def calculate_pass_rate(vet_result: VetResult) -> float:
    """Compute the fraction of tests that passed (0.0 to 1.0).

    Returns 0.0 when there are no test cases (avoids division by zero).
    """
    total = vet_result.total
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, vet_result.passed / total))


def parse_critique_response(response: str) -> tuple[float, str]:
    """Parse the JSON critique response from Ollama.

    Expects ``{"score": <int 0-100>, "reasoning": "<string>"}``.

    Returns:
        A tuple of (score_0_to_1, reasoning).  On any parse failure the
        score defaults to 0.5 and reasoning explains the fallback.
    """
    fallback_score = 0.5
    fallback_reasoning = "Critique response could not be parsed; using default score."

    if not response or not response.strip():
        logger.warning("Empty critique response — using fallback score.")
        return fallback_score, fallback_reasoning

    # Strip markdown fences if the LLM added them despite instructions.
    cleaned = response.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # Remove opening fence (```json or ```) and closing fence (```)
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Malformed critique JSON: %s", response[:200])
        return fallback_score, fallback_reasoning

    if not isinstance(data, dict):
        logger.warning("Critique response is not a JSON object.")
        return fallback_score, fallback_reasoning

    raw_score = data.get("score")
    reasoning = data.get("reasoning", "")

    if not isinstance(reasoning, str):
        reasoning = str(reasoning)

    if raw_score is None:
        logger.warning("Critique JSON missing 'score' key.")
        return fallback_score, reasoning or fallback_reasoning

    try:
        numeric_score = int(raw_score)
    except (ValueError, TypeError):
        logger.warning("Critique 'score' is not an integer: %s", raw_score)
        return fallback_score, reasoning or fallback_reasoning

    # Clamp to valid range then normalise to 0.0-1.0
    clamped = max(0, min(100, numeric_score))
    return clamped / 100.0, reasoning


def calculate_confidence(pass_rate: float, critique_score: float) -> ConfidenceScore:
    """Compute the final confidence score from pass rate and critique score.

    Formula:
        score = int(pass_rate * 0.7 * 100 + critique_score * 0.3 * 100)
        clamped to [0, 100].

    Grade thresholds are determined by the ``ConfidenceScore`` model's
    computed ``grade`` property.

    Args:
        pass_rate: Fraction of tests passed (0.0 to 1.0).
        critique_score: Normalised critique score (0.0 to 1.0).

    Returns:
        A fully populated ``ConfidenceScore``.
    """
    safe_pass = max(0.0, min(1.0, pass_rate))
    safe_critique = max(0.0, min(1.0, critique_score))

    raw = safe_pass * 0.7 * 100 + safe_critique * 0.3 * 100
    score = max(0, min(100, int(raw)))

    return ConfidenceScore(
        score=score,
        pass_rate=safe_pass,
        critique_score=safe_critique,
        explanation=(
            f"Pass rate: {safe_pass:.0%}, "
            f"critique: {safe_critique:.0%} "
            f"-> weighted score {score}/100"
        ),
    )


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def score_fix(vet_result: VetResult, critique_response: str) -> ConfidenceScore:
    """Score a fix by combining test pass rate with LLM critique.

    This is the primary entry point used by the pipeline.

    Args:
        vet_result: The test-execution result to score.
        critique_response: Raw JSON string returned by the Ollama critique call.

    Returns:
        A ``ConfidenceScore`` combining both signals.
    """
    pass_rate = calculate_pass_rate(vet_result)
    critique_score, reasoning = parse_critique_response(critique_response)
    confidence = calculate_confidence(pass_rate, critique_score)

    # Enrich the explanation with critique reasoning when available.
    if reasoning and reasoning != "Critique response could not be parsed; using default score.":
        confidence = confidence.model_copy(
            update={"explanation": f"{confidence.explanation}. Critique: {reasoning}"},
        )

    return confidence
