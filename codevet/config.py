"""Config file discovery and loading for codevet.

Discovery order (first match wins):
    1. Explicit path passed to ``load_config``
    2. ``./codevet.yaml`` in the current working directory
    3. ``~/.config/codevet/config.yaml`` (user global)
    4. Built-in defaults from ``CodevetConfig``

This module deliberately has no YAML dependency — we parse a tiny subset
of YAML (key: value pairs and simple lists) with the stdlib. Users who
need complex YAML can install ``pyyaml`` and we'll prefer it when present.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from codevet.models import CodevetConfig

logger = logging.getLogger(__name__)

_CONFIG_FILENAMES = ("codevet.yaml", "codevet.yml")


def find_config_file(explicit: str | Path | None = None) -> Path | None:
    """Locate a codevet config file using the discovery order.

    Args:
        explicit: Optional explicit path (takes priority over discovery).

    Returns:
        The resolved :class:`Path` to the config file, or ``None`` if no
        config file exists.
    """
    if explicit is not None:
        path = Path(explicit).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")
        return path

    # Search 1: project root (current working directory)
    cwd = Path.cwd()
    for name in _CONFIG_FILENAMES:
        candidate = cwd / name
        if candidate.is_file():
            return candidate.resolve()

    # Search 2: user global
    user_config = Path.home() / ".config" / "codevet" / "config.yaml"
    if user_config.is_file():
        return user_config.resolve()

    return None


def _parse_simple_yaml(text: str) -> dict[str, object]:
    """Parse a tiny subset of YAML (flat key: value pairs + inline lists).

    This handles the default codevet config schema without a YAML
    dependency. For more complex configs, install PyYAML — we'll prefer
    it automatically when available.

    Supported:
        key: value
        key: 42
        key: true
        key: ["a", "b", "c"]
        # comments

    Not supported: nested mappings, multi-line lists, anchors.
    """
    result: dict[str, object] = {}

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        result[key] = _coerce_scalar(value)

    return result


def _coerce_scalar(value: str) -> object:
    """Coerce a stringified YAML scalar to its Python type."""
    if not value:
        return ""

    # Inline list: ["a", "b"]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        items = [item.strip().strip("\"'") for item in inner.split(",")]
        return [item for item in items if item]

    # Boolean
    lower = value.lower()
    if lower in ("true", "yes", "on"):
        return True
    if lower in ("false", "no", "off"):
        return False

    # Integer
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)

    # String (strip quotes if present)
    return value.strip("\"'")


def _load_yaml(path: Path) -> dict[str, object]:
    """Load YAML from *path*. Prefers PyYAML when installed."""
    text = path.read_text(encoding="utf-8")

    try:
        import yaml

        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"Config file must be a YAML mapping, got {type(data).__name__}"
            )
        return data
    except ImportError:
        logger.debug("PyYAML not installed; using fallback parser")
        return _parse_simple_yaml(text)


def load_config(explicit: str | Path | None = None) -> CodevetConfig:
    """Load codevet configuration from a config file or defaults.

    Args:
        explicit: Optional explicit path to a config file.

    Returns:
        A validated :class:`CodevetConfig` instance. Returns defaults when
        no config file is found.
    """
    config_path = find_config_file(explicit)

    if config_path is None:
        logger.debug("No config file found; using defaults")
        return CodevetConfig()

    logger.info("Loading config from %s", config_path)
    raw = _load_yaml(config_path)

    try:
        return CodevetConfig(**cast("dict[str, Any]", raw))
    except Exception as exc:
        raise ValueError(
            f"Invalid codevet config at {config_path}: {exc}"
        ) from exc


def example_config_yaml() -> str:
    """Return a commented example config file for users to copy.

    The same content is shipped as ``codevet.yaml.example`` at the repo
    root so users can find it without running a command.
    """
    return """\
# ============================================================================
# codevet.yaml  —  codevet configuration file
# ============================================================================
#
# Where this file lives (first match wins):
#   1. ./codevet.yaml in your project root  (recommended)
#   2. ~/.config/codevet/config.yaml        (user-global, all projects)
#   3. Built-in defaults (this file documents them)
#
# Override any setting from the command line:
#   codevet fix app.py --model qwen2.5-coder:14b --timeout 600
#
# To create a fresh copy of this file in your project, run:
#   codevet init-config
#
# ----------------------------------------------------------------------------
# Ollama model
# ----------------------------------------------------------------------------
# Any model you've pulled with `ollama pull <name>` works. The default is
# qwen2.5-coder:7b, which is the smallest model strong enough to produce
# usable security/edge-case test fixes.
#
# Codevet runs a preflight hardware-fit check (powered by llmfit) before
# loading the model, so you can safely experiment — models that won't fit
# your RAM/VRAM are rejected with a clear error before any inference runs.
#
# Recommended models by hardware tier:
#   - 8 GB RAM, no GPU      -> qwen2.5-coder:1.5b   (fast, weaker fixes)
#   - 16 GB RAM, no GPU     -> qwen2.5-coder:7b     (default, balanced)
#   - 32 GB RAM, any GPU    -> qwen2.5-coder:14b    (best fix quality)
#   - 64 GB RAM + GPU       -> qwen2.5-coder:32b    (maximum quality)
#
# To check a model's hardware fit before configuring it, run:
#   codevet preflight <model>
model: qwen2.5-coder:7b

# ----------------------------------------------------------------------------
# Docker sandbox
# ----------------------------------------------------------------------------
# Docker image for the sandbox. Codevet builds its own custom image
# (codevet-sandbox:0.1.0) on first run with pytest preinstalled.
# To use a different base, change this value.
image: python:3.11-slim

# Hard timeout per sandbox run, in seconds.
# Clamped to [5, 1800] (30 min ceiling).
#
# Default is 120s, which is plenty for sandbox test execution. The Ollama
# inference calls (test gen + fix iterations) are NOT bounded by this —
# they're bounded by your model size and CPU/GPU. On CPU, qwen2.5-coder:7b
# can take 5-7 min per call, and 14b can take 10-15 min per call.
timeout_seconds: 120

# Max fix iterations. Hard-capped at 3 by codevet — higher values waste
# tokens without typically improving the result.
max_iterations: 3

# Container memory cap (Docker format: "256m", "1g", "2g", ...)
mem_limit: 256m

# Cap on the number of processes inside the container.
pids_limit: 64

# ----------------------------------------------------------------------------
# Confidence scoring
# ----------------------------------------------------------------------------
# The final confidence score is a weighted blend of two signals:
#
#   score = (test_pass_rate * pass_weight + critique_score * critique_weight) * 100
#
# pass_weight + critique_weight MUST sum to 1.0. The defaults (0.7 / 0.3)
# err on the side of trusting the test results over the LLM's self-grade.
# If you trust your model's judgment more than its test fixes, raise the
# critique_weight (e.g. 0.5 / 0.5).
confidence_pass_weight: 0.7
confidence_critique_weight: 0.3
"""
