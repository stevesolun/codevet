"""Preflight model-fit check powered by ``llmfit``.

``llmfit`` (https://github.com/AlexsJones/llmfit) is a Rust CLI written
by Alex Jones that detects host hardware (RAM, CPU, GPU) and scores
LLM models against it. Codevet ships with automatic download-on-demand
for the correct prebuilt llmfit binary, keyed to the host platform, so
users never have to install it manually.

Credits: llmfit is © 2024-2026 Alex Jones, MIT licensed. We embed it
purely as a client; the binary itself is not redistributed in this
repository. See: https://github.com/AlexsJones/llmfit

The check is **mandatory unless bypassed**:
- If the model is "Too Tight", we BLOCK with a hard error.
- If the model is "Marginal", we WARN and continue.
- If the model is "Good" or "Perfect", we silently proceed.
- If llmfit cannot classify the model, we WARN and continue.

Users can bypass the check with ``--skip-preflight`` on the CLI.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from codevet.models import LLMFIT_SUBPROCESS_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# llmfit binary auto-installer
# ---------------------------------------------------------------------------

LLMFIT_REPO = "AlexsJones/llmfit"
LLMFIT_LATEST_API = f"https://api.github.com/repos/{LLMFIT_REPO}/releases/latest"

# Fallback version if we can't reach the GitHub API (offline, rate limited).
LLMFIT_FALLBACK_VERSION = "v0.9.2"

# Cache the latest-version lookup for 24 hours to avoid rate-limiting.
_VERSION_CACHE_TTL_SECONDS = 24 * 60 * 60

# Asset suffix patterns keyed by (system, machine).
# We build the filename once we know the version:
# f"llmfit-{version}-{suffix}"
_ASSET_SUFFIXES: dict[tuple[str, str], str] = {
    ("linux", "x86_64"): "x86_64-unknown-linux-gnu.tar.gz",
    ("linux", "amd64"): "x86_64-unknown-linux-gnu.tar.gz",
    ("linux", "aarch64"): "aarch64-unknown-linux-gnu.tar.gz",
    ("linux", "arm64"): "aarch64-unknown-linux-gnu.tar.gz",
    ("darwin", "x86_64"): "x86_64-apple-darwin.tar.gz",
    ("darwin", "arm64"): "aarch64-apple-darwin.tar.gz",
    ("darwin", "aarch64"): "aarch64-apple-darwin.tar.gz",
    ("windows", "amd64"): "x86_64-pc-windows-msvc.zip",
    ("windows", "x86_64"): "x86_64-pc-windows-msvc.zip",
    ("windows", "arm64"): "aarch64-pc-windows-msvc.zip",
}


def _cache_dir() -> Path:
    """Return the codevet cache directory for bundled binaries."""
    home = Path.home()
    return home / ".cache" / "codevet" / "bin"


def _version_cache_path() -> Path:
    """Return the path to the cached latest-version metadata file."""
    return _cache_dir() / "llmfit-version.json"


def _llmfit_binary_name() -> str:
    """Return the platform-appropriate filename for the llmfit binary."""
    return "llmfit.exe" if platform.system() == "Windows" else "llmfit"


def _llmfit_binary_path() -> Path:
    """Return the expected path to the cached llmfit binary."""
    return _cache_dir() / _llmfit_binary_name()


def _asset_for_host(version: str) -> str | None:
    """Return the llmfit release asset filename for the current host."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    suffix = _ASSET_SUFFIXES.get((system, machine))
    if suffix is None:
        return None
    return f"llmfit-{version}-{suffix}"


def _fetch_latest_version() -> str:
    """Return the latest llmfit tag from GitHub, cached locally for 24h.

    Falls back to :data:`LLMFIT_FALLBACK_VERSION` when the network is
    unavailable or GitHub rate-limits us.
    """
    cache = _version_cache_path()
    if cache.is_file():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            fetched_at = float(payload.get("fetched_at", 0))
            version = payload.get("version")
            if (
                isinstance(version, str)
                and version
                and time.time() - fetched_at < _VERSION_CACHE_TTL_SECONDS
            ):
                return version
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    try:
        req = urllib.request.Request(  # noqa: S310 - fixed GitHub API host
            LLMFIT_LATEST_API,
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
        tag = data.get("tag_name")
        if not isinstance(tag, str) or not tag:
            raise ValueError("GitHub API returned no tag_name")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.debug("Could not fetch latest llmfit version: %s", exc)
        return LLMFIT_FALLBACK_VERSION

    # Persist the fetched version to the cache.
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({"version": tag, "fetched_at": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        pass

    return tag


def _find_llmfit() -> str | None:
    """Locate llmfit: prefer PATH, fall back to the codevet cache."""
    on_path = shutil.which("llmfit")
    if on_path:
        return on_path

    cached = _llmfit_binary_path()
    if cached.is_file():
        return str(cached)
    return None


def ensure_llmfit(auto_install: bool = True) -> str | None:
    """Locate or download the llmfit binary.

    On first call this fetches the latest llmfit release tag from GitHub
    (cached 24h), downloads the matching prebuilt binary for the host,
    and stores it under ``~/.cache/codevet/bin``. Subsequent calls reuse
    the cached binary, so the network hit is one-time per version.

    Args:
        auto_install: If True, download the binary when missing. If False,
            only return an existing installation.

    Returns:
        Path to the llmfit executable, or None if it could not be found
        and ``auto_install`` is False (or the host is unsupported).
    """
    existing = _find_llmfit()
    if existing:
        return existing

    if not auto_install:
        return None

    version = _fetch_latest_version()
    asset = _asset_for_host(version)
    if asset is None:
        logger.warning(
            "No llmfit prebuilt binary for platform %s/%s",
            platform.system(),
            platform.machine(),
        )
        return None

    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)

    url = f"https://github.com/{LLMFIT_REPO}/releases/download/{version}/{asset}"
    archive_path = cache / asset

    try:
        logger.info("Downloading llmfit %s from %s", version, url)
        _download(url, archive_path)
        _extract(archive_path, cache)
    except Exception as exc:
        logger.warning("Failed to auto-install llmfit: %s", exc)
        return None
    finally:
        if archive_path.exists():
            archive_path.unlink(missing_ok=True)

    binary = _llmfit_binary_path()
    if not binary.exists():
        logger.warning("llmfit archive extracted but binary not found at %s", binary)
        return None

    # Mark as executable on POSIX systems.
    if platform.system() != "Windows":
        binary.chmod(0o755)

    return str(binary)


def _download(url: str, dest: Path) -> None:
    """Download *url* to *dest* using stdlib urllib."""
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        dest.write_bytes(response.read())


def _extract(archive: Path, target_dir: Path) -> None:
    """Extract an llmfit archive, flattening any nested directory structure."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for zip_name in zf.namelist():
                if zip_name.endswith(("/", "\\")):
                    continue
                filename = Path(zip_name).name
                if not filename.startswith("llmfit"):
                    continue
                dest = target_dir / filename
                with zf.open(zip_name) as src, dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        return

    with tarfile.open(archive, "r:gz") as tf:
        for tar_member in tf.getmembers():
            if not tar_member.isfile():
                continue
            filename = Path(tar_member.name).name
            if not filename.startswith("llmfit"):
                continue
            extracted = tf.extractfile(tar_member)
            if extracted is None:
                continue
            (target_dir / filename).write_bytes(extracted.read())


class FitLevel(StrEnum):
    """Human-readable fit categories returned by llmfit."""

    PERFECT = "perfect"
    GOOD = "good"
    MARGINAL = "marginal"
    TOO_TIGHT = "too_tight"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FitResult:
    """Result of a preflight fit check."""

    fit: FitLevel
    model: str
    reason: str
    required_ram_gb: float | None = None
    llmfit_installed: bool = True

    @property
    def should_block(self) -> bool:
        """Whether this result should prevent the pipeline from running."""
        return self.fit == FitLevel.TOO_TIGHT

    @property
    def should_warn(self) -> bool:
        """Whether this result should print a user-facing warning."""
        return self.fit == FitLevel.MARGINAL


class PreflightError(Exception):
    """Raised when the preflight check conclusively fails (model won't fit)."""


def check_model_fit(
    model: str, context: int = 8192, auto_install: bool = True
) -> FitResult:
    """Run ``llmfit plan`` against *model* and classify the fit.

    On first use the llmfit binary is downloaded automatically from the
    upstream GitHub release and cached in ``~/.cache/codevet/bin``. No
    manual install step is required for supported platforms.

    Args:
        model: An Ollama-style model identifier (e.g. ``qwen2.5-coder:7b``).
            llmfit speaks in HuggingFace identifiers, so we translate the
            Ollama name to the upstream HuggingFace ID.
        context: The context window size the codevet pipeline will use.
        auto_install: Whether to download llmfit if not already present.

    Returns:
        A :class:`FitResult` describing whether the model fits on the
        current machine.
    """
    binary = ensure_llmfit(auto_install=auto_install)
    if binary is None:
        return FitResult(
            fit=FitLevel.UNKNOWN,
            model=model,
            reason=(
                "llmfit is not available and auto-install did not succeed. "
                "Please install it manually from "
                "https://github.com/AlexsJones/llmfit"
            ),
            llmfit_installed=False,
        )

    # Strip the Ollama tag (``:7b``) and normalise to a HuggingFace form
    # that llmfit is likely to recognise. llmfit lookup is lenient.
    hf_model = _ollama_to_hf(model)

    try:
        proc = subprocess.run(
            [binary, "plan", hf_model, "--context", str(context), "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=LLMFIT_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("llmfit invocation failed: %s", exc)
        return FitResult(
            fit=FitLevel.UNKNOWN,
            model=model,
            reason=f"llmfit failed to execute: {exc}",
        )

    if proc.returncode != 0:
        return FitResult(
            fit=FitLevel.UNKNOWN,
            model=model,
            reason=(
                f"llmfit could not classify '{model}' "
                f"(exit {proc.returncode}): {proc.stderr.strip()[:200]}"
            ),
        )

    try:
        payload: Mapping[str, object] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return FitResult(
            fit=FitLevel.UNKNOWN,
            model=model,
            reason=f"llmfit returned non-JSON output: {exc}",
        )

    return _parse_llmfit_payload(payload, model)


def _parse_llmfit_payload(payload: Mapping[str, object], model: str) -> FitResult:
    """Extract a :class:`FitResult` from the llmfit JSON payload.

    The llmfit ``plan`` command returns:
      - ``current.fit_level`` — the actual fit on this host (primary signal)
      - ``current.run_mode`` — which execution path (gpu / cpu_offload / cpu_only)
      - ``recommended.ram_gb`` / ``minimum.ram_gb`` — memory requirements
      - ``run_paths[*].fit_level`` — per-path fallbacks

    We defensively search across several key names so older llmfit
    versions still work.
    """
    fit_str = _extract_fit_string(payload)
    required = _extract_recommended_memory(payload)
    run_mode = _extract_run_mode(payload)

    if fit_str is None:
        return FitResult(
            fit=FitLevel.UNKNOWN,
            model=model,
            reason="llmfit output did not contain a fit classification.",
            required_ram_gb=required,
        )

    level = _normalise_fit_level(fit_str)
    reason = _build_reason(level, required, run_mode)

    return FitResult(
        fit=level,
        model=model,
        reason=reason,
        required_ram_gb=required,
    )


def _extract_fit_string(payload: Mapping[str, object]) -> str | None:
    """Search the payload for a fit classification string.

    Priority order:
      1. ``current.fit_level`` (llmfit 0.9+)
      2. Top-level ``fit`` / ``fit_level`` / ``classification`` / ``status``
      3. Nested ``result.fit_level``
      4. Best of ``run_paths[*].fit_level`` (worst-case wins)
    """
    current = payload.get("current")
    if isinstance(current, dict):
        value = current.get("fit_level")
        if isinstance(value, str):
            return value.lower().replace(" ", "_")

    for key in ("fit", "fit_level", "classification", "status"):
        value = payload.get(key)
        if isinstance(value, str):
            return value.lower().replace(" ", "_")

    result = payload.get("result")
    if isinstance(result, dict):
        nested = _extract_fit_string(result)
        if nested is not None:
            return nested

    run_paths = payload.get("run_paths")
    if isinstance(run_paths, list):
        best_raw: str | None = None
        best_rank = -1
        rank = {
            FitLevel.PERFECT: 4,
            FitLevel.GOOD: 3,
            FitLevel.MARGINAL: 2,
            FitLevel.TOO_TIGHT: 1,
        }
        for entry in run_paths:
            if not isinstance(entry, dict):
                continue
            if entry.get("feasible") is False:
                continue
            fit = entry.get("fit_level")
            if not isinstance(fit, str):
                continue
            level = _normalise_fit_level(fit.lower().replace(" ", "_"))
            if level == FitLevel.UNKNOWN:
                continue
            if rank.get(level, 0) > best_rank:
                best_rank = rank.get(level, 0)
                best_raw = fit.lower().replace(" ", "_")
        return best_raw

    return None


def _extract_recommended_memory(payload: Mapping[str, object]) -> float | None:
    """Return the recommended RAM (GB) for this model, if available."""
    recommended = payload.get("recommended")
    if isinstance(recommended, dict):
        ram = recommended.get("ram_gb")
        if isinstance(ram, int | float):
            return float(ram)

    minimum = payload.get("minimum")
    if isinstance(minimum, dict):
        ram = minimum.get("ram_gb")
        if isinstance(ram, int | float):
            return float(ram)

    return None


def _extract_run_mode(payload: Mapping[str, object]) -> str | None:
    """Return the run mode llmfit selected for the host (gpu / cpu / ...)."""
    current = payload.get("current")
    if isinstance(current, dict):
        mode = current.get("run_mode")
        if isinstance(mode, str):
            return mode
    return None


def _normalise_fit_level(raw: str) -> FitLevel:
    """Map any llmfit spelling to our :class:`FitLevel` enum.

    llmfit uses CamelCase enum variants like ``"TooTight"`` or
    ``"Marginal"``. Legacy releases use ``"Too Tight"`` / ``"too_tight"``.
    We strip all separators and lowercase before lookup.
    """
    canonical = raw.replace("_", "").replace(" ", "").replace("-", "").lower()
    lookup = {
        "perfect": FitLevel.PERFECT,
        "good": FitLevel.GOOD,
        "marginal": FitLevel.MARGINAL,
        "tight": FitLevel.MARGINAL,
        "tootight": FitLevel.TOO_TIGHT,
        "notfeasible": FitLevel.TOO_TIGHT,
        "insufficient": FitLevel.TOO_TIGHT,
        "nofit": FitLevel.TOO_TIGHT,
    }
    return lookup.get(canonical, FitLevel.UNKNOWN)


def _build_reason(
    level: FitLevel,
    required: float | None,
    run_mode: str | None,
) -> str:
    """Compose a human-readable sentence describing the fit result."""
    mem = f" (recommended: {required:.1f} GB RAM)" if required else ""
    mode = f" via {run_mode}" if run_mode else ""

    match level:
        case FitLevel.PERFECT:
            return f"Model fits perfectly on this machine{mode}{mem}."
        case FitLevel.GOOD:
            return f"Model fits with headroom{mode}{mem}."
        case FitLevel.MARGINAL:
            return (
                f"Model fits only marginally{mode}{mem}. "
                "Expect slow inference and possible OOM on large contexts."
            )
        case FitLevel.TOO_TIGHT:
            return (
                f"Model is too large for this machine{mem}. "
                "Pick a smaller model or add more RAM/VRAM."
            )
        case FitLevel.UNKNOWN:
            return "Could not determine whether the model fits."


def _ollama_to_hf(model: str) -> str:
    """Convert an Ollama model identifier to the matching HuggingFace ID.

    llmfit queries HuggingFace by default, so we map the most common
    coder models here. Unknown models are passed through verbatim —
    llmfit is lenient and may resolve them via its own search.
    """
    mapping = {
        "qwen2.5-coder:1.5b": "Qwen/Qwen2.5-Coder-1.5B-Instruct",
        "qwen2.5-coder:3b": "Qwen/Qwen2.5-Coder-3B-Instruct",
        "qwen2.5-coder:7b": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "qwen2.5-coder:14b": "Qwen/Qwen2.5-Coder-14B-Instruct",
        "qwen2.5-coder:32b": "Qwen/Qwen2.5-Coder-32B-Instruct",
        "deepseek-coder-v2:16b": "deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct",
        "codellama:7b": "codellama/CodeLlama-7b-Instruct-hf",
        "codellama:13b": "codellama/CodeLlama-13b-Instruct-hf",
        "codellama:34b": "codellama/CodeLlama-34b-Instruct-hf",
        "codestral:22b": "mistralai/Codestral-22B-v0.1",
        "gemma2:9b": "google/gemma-2-9b-it",
        "llama3.1:8b": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "granite-code:8b": "ibm-granite/granite-8b-code-instruct-4k",
        "starcoder2:15b": "bigcode/starcoder2-15b",
    }
    return mapping.get(model, model)
