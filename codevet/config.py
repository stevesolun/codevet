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
    """Return a commented example config file for users to copy."""
    return """\
# codevet.yaml — codevet configuration file
# Place this file in your project root or at ~/.config/codevet/config.yaml

# Ollama model for test generation and auto-fixing.
# Pick any model you have pulled with `ollama pull <name>`.
# codevet runs a preflight hardware-fit check before loading,
# so you can safely experiment — too-large models will be rejected
# before they OOM your machine.
#
# To check a model's hardware fit first, run:
#   codevet preflight <model>
#
# Install llmfit for preflight validation:
#   https://github.com/AlexsJones/llmfit
model: gemma2:9b

# Docker image for the sandbox (must have Python installed).
# Options: python:3.11-slim, python:3.12-slim, python:3.13-slim
image: python:3.11-slim

# Hard timeout in seconds (clamped to [5, 300])
timeout_seconds: 30

# Max fix iterations (hard capped at 3)
max_iterations: 3

# Container memory limit
mem_limit: 256m

# Max processes inside container
pids_limit: 64
"""
