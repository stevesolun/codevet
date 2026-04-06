"""Pydantic data models for codevet."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 – Pydantic needs Path at runtime for field resolution
from typing import Literal

from pydantic import BaseModel, computed_field, field_validator, model_validator


class SandboxConfig(BaseModel):
    """Docker sandbox configuration."""

    image: str = "python:3.11-slim"
    timeout_seconds: int = 30
    read_only: bool = True
    network_disabled: bool = True
    tmpfs_mounts: dict[str, str] = {"/tmp": "size=100m"}
    security_opt: list[str] = ["no-new-privileges"]
    mem_limit: str = "256m"
    pids_limit: int = 64
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
    model: str = "gemma2:9b"

    # Docker image for the sandbox (must have Python installed)
    image: str = "python:3.11-slim"

    # Hard timeout in seconds for each sandbox run
    timeout_seconds: int = 30

    # Maximum fix iterations (hard capped at 3 for safety)
    max_iterations: int = 3

    # Container memory limit (Docker format: "256m", "1g", etc.)
    mem_limit: str = "256m"

    # Maximum number of processes inside the container
    pids_limit: int = 64

    # Docker security options (defaults hardened)
    security_opt: list[str] = ["no-new-privileges"]

    # Linux capabilities to drop (ALL = drop everything)
    cap_drop: list[str] = ["ALL"]

    @field_validator("max_iterations")
    @classmethod
    def _cap_iterations(cls, v: int) -> int:
        """Hard cap at 3 iterations for safety."""
        return min(max(v, 1), 3)

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, v: int) -> int:
        """Clamp timeout to [5, 300] seconds."""
        return min(max(v, 5), 300)


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
        """Enforce max 3 iterations."""
        if v > 3:
            raise ValueError("iterations_used must not exceed 3")
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
