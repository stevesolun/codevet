"""Confidence scoring for codevet fix results.

Weights for the two components (pass-rate vs LLM critique) are surfaced as
function arguments and default to ``DEFAULT_CONFIDENCE_PASS_WEIGHT`` and
``DEFAULT_CONFIDENCE_CRITIQUE_WEIGHT`` from :mod:`codevet.models`. The CLI
threads ``CodevetConfig`` values through, so end users can tune the weights
in ``codevet.yaml``.
"""
from __future__ import annotations

import json
import logging

from codevet.models import (
    DEFAULT_CONFIDENCE_CRITIQUE_WEIGHT,
    DEFAULT_CONFIDENCE_PASS_WEIGHT,
    ConfidenceScore,
    VetResult,
)

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


def calculate_confidence(
    pass_rate: float,
    critique_score: float,
    *,
    pass_weight: float = DEFAULT_CONFIDENCE_PASS_WEIGHT,
    critique_weight: float = DEFAULT_CONFIDENCE_CRITIQUE_WEIGHT,
) -> ConfidenceScore:
    """Compute the final confidence score from pass rate and critique score.

    Formula::

        score = int(pass_rate * pass_weight * 100
                    + critique_score * critique_weight * 100)
        clamped to [0, 100].

    Grade thresholds are determined by the ``ConfidenceScore`` model's
    computed ``grade`` property.

    Args:
        pass_rate: Fraction of tests passed (0.0 to 1.0).
        critique_score: Normalised critique score (0.0 to 1.0).
        pass_weight: Weight for the pass-rate component (default 0.7,
            tunable via ``codevet.yaml``).
        critique_weight: Weight for the critique component (default 0.3,
            tunable via ``codevet.yaml``).

    Returns:
        A fully populated ``ConfidenceScore``.
    """
    safe_pass = max(0.0, min(1.0, pass_rate))
    safe_critique = max(0.0, min(1.0, critique_score))

    raw = safe_pass * pass_weight * 100 + safe_critique * critique_weight * 100
    score = max(0, min(100, int(raw)))

    return ConfidenceScore(
        score=score,
        pass_rate=safe_pass,
        critique_score=safe_critique,
        explanation=(
            f"Pass rate: {safe_pass:.0%} (w={pass_weight:.2f}), "
            f"critique: {safe_critique:.0%} (w={critique_weight:.2f}) "
            f"-> weighted score {score}/100"
        ),
    )


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def score_fix(
    vet_result: VetResult,
    critique_response: str,
    *,
    pass_weight: float = DEFAULT_CONFIDENCE_PASS_WEIGHT,
    critique_weight: float = DEFAULT_CONFIDENCE_CRITIQUE_WEIGHT,
) -> ConfidenceScore:
    """Score a fix by combining test pass rate with LLM critique.

    This is the primary entry point used by the pipeline.

    Args:
        vet_result: The test-execution result to score.
        critique_response: Raw JSON string returned by the Ollama critique call.
        pass_weight: Weight for the pass-rate component (default 0.7).
        critique_weight: Weight for the critique component (default 0.3).

    Returns:
        A ``ConfidenceScore`` combining both signals.
    """
    pass_rate = calculate_pass_rate(vet_result)
    critique_score, reasoning = parse_critique_response(critique_response)
    confidence = calculate_confidence(
        pass_rate,
        critique_score,
        pass_weight=pass_weight,
        critique_weight=critique_weight,
    )

    # Enrich the explanation with critique reasoning when available.
    if reasoning and reasoning != "Critique response could not be parsed; using default score.":
        confidence = confidence.model_copy(
            update={"explanation": f"{confidence.explanation}. Critique: {reasoning}"},
        )

    return confidence
