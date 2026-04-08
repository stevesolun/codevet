"""Pydantic data models for codevet.

All tunable constants live at the top of this module as named
``UPPER_SNAKE_CASE`` values so they are easy to find and audit. The
``CodevetConfig`` class exposes the user-facing knobs; the constants
below are sanity guardrails (hard caps) that the validators enforce.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 – Pydantic needs Path at runtime for field resolution
from typing import Literal

from pydantic import BaseModel, computed_field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Module constants — guardrail bounds and default values.
# Override the user-facing values via codevet.yaml; the bounds below are
# enforced by validators and cannot be exceeded.
# ---------------------------------------------------------------------------

#: Lower bound on the per-call sandbox timeout, in seconds.
MIN_TIMEOUT_SECONDS = 5
#: Upper bound on the per-call sandbox timeout, in seconds (30 min ceiling).
MAX_TIMEOUT_SECONDS = 1800
#: Default per-call sandbox timeout, in seconds.
DEFAULT_TIMEOUT_SECONDS = 120

#: Hard cap on the number of fix iterations. Higher values waste tokens
#: without typically improving the result.
MAX_FIX_ITERATIONS_HARD_CAP = 3
#: Default number of fix iterations.
DEFAULT_FIX_ITERATIONS = 3

#: Weight applied to the test pass-rate component of the confidence score.
DEFAULT_CONFIDENCE_PASS_WEIGHT = 0.7
#: Weight applied to the LLM self-critique component of the confidence score.
DEFAULT_CONFIDENCE_CRITIQUE_WEIGHT = 0.3

#: Default Docker image used for the sandbox container.
DEFAULT_SANDBOX_IMAGE = "python:3.11-slim"
#: Default Ollama model.
DEFAULT_MODEL = "qwen2.5-coder:7b"
#: Default container memory cap.
DEFAULT_MEM_LIMIT = "256m"
#: Default cap on the number of processes inside the container.
DEFAULT_PIDS_LIMIT = 64

#: HTTP pool timeout for the Docker SDK client (Windows + WSL2 needs this
#: above the docker-py default of 60s).
DOCKER_HTTP_TIMEOUT_SECONDS = 300
#: Subprocess timeout for the llmfit preflight binary.
LLMFIT_SUBPROCESS_TIMEOUT_SECONDS = 30


class SandboxConfig(BaseModel):
    """Docker sandbox configuration."""

    image: str = DEFAULT_SANDBOX_IMAGE
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    read_only: bool = True
    network_disabled: bool = True
    tmpfs_mounts: dict[str, str] = {"/tmp": "size=100m"}
    security_opt: list[str] = ["no-new-privileges"]
    mem_limit: str = DEFAULT_MEM_LIMIT
    pids_limit: int = DEFAULT_PIDS_LIMIT
    cap_drop: list[str] = ["ALL"]
    project_dir: Path


class CodevetConfig(BaseModel):
    """User-configurable settings loaded from codevet.yaml.

    Discovery order (first found wins):
    1. --config <path> CLI flag
    2. ./codevet.yaml (project root)
    3. ~/.config/codevet/config.yaml (user global)
    4. Built-in defaults (this class)

    CLI flags always take precedence over config file values.
    """

    # Ollama model to use for test generation and fixing
    model: str = DEFAULT_MODEL

    # Docker image for the sandbox (must have Python installed)
    image: str = DEFAULT_SANDBOX_IMAGE

    # Hard timeout in seconds for each sandbox run.
    # Default is 120s. Bump to 600+ for slow CPU LLM inference (7B+ on CPU).
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    # Maximum fix iterations (hard capped at MAX_FIX_ITERATIONS_HARD_CAP)
    max_iterations: int = DEFAULT_FIX_ITERATIONS

    # Container memory limit (Docker format: "256m", "1g", etc.)
    mem_limit: str = DEFAULT_MEM_LIMIT

    # Maximum number of processes inside the container
    pids_limit: int = DEFAULT_PIDS_LIMIT

    # Docker security options (defaults hardened)
    security_opt: list[str] = ["no-new-privileges"]

    # Linux capabilities to drop (ALL = drop everything)
    cap_drop: list[str] = ["ALL"]

    # Confidence-score component weights. Must sum to 1.0.
    # The pass_weight applies to the test pass-rate; the critique_weight
    # applies to the LLM self-critique score.
    confidence_pass_weight: float = DEFAULT_CONFIDENCE_PASS_WEIGHT
    confidence_critique_weight: float = DEFAULT_CONFIDENCE_CRITIQUE_WEIGHT

    @field_validator("max_iterations")
    @classmethod
    def _cap_iterations(cls, v: int) -> int:
        """Hard cap at MAX_FIX_ITERATIONS_HARD_CAP for safety."""
        return min(max(v, 1), MAX_FIX_ITERATIONS_HARD_CAP)

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, v: int) -> int:
        """Clamp timeout to [MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS]."""
        return min(max(v, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)

    @model_validator(mode="after")
    def _validate_confidence_weights(self) -> CodevetConfig:
        """Confidence weights must each be in [0, 1] and sum to ~1.0."""
        if not 0.0 <= self.confidence_pass_weight <= 1.0:
            raise ValueError("confidence_pass_weight must be between 0.0 and 1.0")
        if not 0.0 <= self.confidence_critique_weight <= 1.0:
            raise ValueError("confidence_critique_weight must be between 0.0 and 1.0")
        total = self.confidence_pass_weight + self.confidence_critique_weight
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"confidence_pass_weight + confidence_critique_weight must "
                f"sum to 1.0, got {total:.3f}"
            )
        return self


class SandboxResult(BaseModel):
    """Result from running code in sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_seconds: float


class GeneratedTest(BaseModel):
    """A single generated test case."""

    name: str
    code: str
    category: Literal["unit", "edge", "security", "performance"]


class VetResult(BaseModel):
    """Result from vetting (test generation + execution)."""

    test_cases: list[GeneratedTest]
    passed: int
    failed: int
    errors: int
    raw_output: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        """Total number of test cases."""
        return len(self.test_cases)


class FixAttempt(BaseModel):
    """A single fix attempt."""

    iteration: int
    patch: str
    test_result: VetResult
    explanation: str


class FixResult(BaseModel):
    """Result from the auto-fix loop."""

    original_code: str
    fixed_code: str
    attempts: list[FixAttempt]
    success: bool
    iterations_used: int

    @field_validator("iterations_used")
    @classmethod
    def validate_iterations_used(cls, v: int) -> int:
        """Enforce MAX_FIX_ITERATIONS_HARD_CAP."""
        if v > MAX_FIX_ITERATIONS_HARD_CAP:
            raise ValueError(
                f"iterations_used must not exceed {MAX_FIX_ITERATIONS_HARD_CAP}"
            )
        return v


class ConfidenceScore(BaseModel):
    """Confidence scoring result."""

    score: int
    pass_rate: float
    critique_score: float
    explanation: str

    @field_validator("score")
    @classmethod
    def clamp_score(cls, v: int) -> int:
        """Clamp score to 0-100 range."""
        return max(0, min(100, v))

    @model_validator(mode="after")
    def validate_rates(self) -> ConfidenceScore:
        """Validate that pass_rate and critique_score are in [0.0, 1.0]."""
        if not 0.0 <= self.pass_rate <= 1.0:
            raise ValueError("pass_rate must be between 0.0 and 1.0")
        if not 0.0 <= self.critique_score <= 1.0:
            raise ValueError("critique_score must be between 0.0 and 1.0")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def grade(self) -> str:
        """Compute letter grade based on score thresholds."""
        if self.score >= 90:
            return "A"
        if self.score >= 80:
            return "B"
        if self.score >= 70:
            return "C"
        if self.score >= 60:
            return "D"
        return "F"


class CodevetOutput(BaseModel):
    """Complete pipeline output."""

    file_path: str
    original_code: str
    fixed_code: str | None
    confidence: ConfidenceScore
    vet_result: VetResult
    fix_result: FixResult | None
    model_used: str
    duration_seconds: float
