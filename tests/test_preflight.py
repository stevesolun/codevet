"""Tests for the preflight hardware-fit check."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from codevet.preflight import (
    FitLevel,
    FitResult,
    _ollama_to_hf,
    _parse_llmfit_payload,
    check_model_fit,
)


class TestOllamaToHfMapping:
    """Ollama → HuggingFace identifier mapping."""

    def test_known_qwen_model_maps_to_hf(self) -> None:
        assert _ollama_to_hf("qwen2.5-coder:7b") == "Qwen/Qwen2.5-Coder-7B-Instruct"

    def test_known_deepseek_model_maps_to_hf(self) -> None:
        assert (
            _ollama_to_hf("deepseek-coder-v2:16b")
            == "deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct"
        )

    def test_unknown_model_passes_through(self) -> None:
        """Unknown models should be returned unchanged so llmfit can try to resolve."""
        assert _ollama_to_hf("mystery-model:7b") == "mystery-model:7b"


class TestParseLlmfitPayload:
    """JSON payload parsing."""

    def test_perfect_fit_with_ram_info(self) -> None:
        payload = {
            "current": {"fit_level": "Perfect", "run_mode": "Gpu"},
            "recommended": {"ram_gb": 8.0, "vram_gb": 7.0},
        }
        result = _parse_llmfit_payload(payload, "qwen2.5-coder:7b")
        assert result.fit == FitLevel.PERFECT
        assert result.required_ram_gb == 8.0
        assert "perfectly" in result.reason.lower()
        assert "gpu" in result.reason.lower()

    def test_marginal_fit_triggers_warning(self) -> None:
        payload = {
            "current": {"fit_level": "Marginal", "run_mode": "CpuOnly"},
            "recommended": {"ram_gb": 16.0},
        }
        result = _parse_llmfit_payload(payload, "qwen2.5-coder:14b")
        assert result.fit == FitLevel.MARGINAL
        assert result.should_warn is True
        assert result.should_block is False

    def test_too_tight_blocks_execution(self) -> None:
        payload = {
            "current": {"fit_level": "Too Tight"},
            "recommended": {"ram_gb": 64.0},
        }
        result = _parse_llmfit_payload(payload, "qwen2.5-coder:32b")
        assert result.fit == FitLevel.TOO_TIGHT
        assert result.should_block is True

    def test_run_paths_fallback(self) -> None:
        """When current.fit_level is missing, best feasible run_path wins."""
        payload = {
            "run_paths": [
                {"path": "gpu", "feasible": False, "fit_level": "Too Tight"},
                {"path": "cpu_only", "feasible": True, "fit_level": "Good"},
                {"path": "cpu_offload", "feasible": True, "fit_level": "Marginal"},
            ],
            "recommended": {"ram_gb": 12.0},
        }
        result = _parse_llmfit_payload(payload, "x:7b")
        # Best feasible path is "Good" (cpu_only).
        assert result.fit == FitLevel.GOOD

    def test_missing_fit_field_returns_unknown(self) -> None:
        payload = {"some_other_field": "value"}
        result = _parse_llmfit_payload(payload, "mystery:7b")
        assert result.fit == FitLevel.UNKNOWN
        assert "did not contain" in result.reason.lower()

    def test_nested_result_object(self) -> None:
        """Some llmfit versions nest the verdict under a 'result' key."""
        payload = {"result": {"fit": "good"}}
        result = _parse_llmfit_payload(payload, "qwen2.5-coder:7b")
        assert result.fit == FitLevel.GOOD

    def test_alternative_fit_spellings(self) -> None:
        """'insufficient' and 'no_fit' both map to TOO_TIGHT."""
        for spelling in ("insufficient", "no_fit", "tight"):
            payload = {"fit": spelling}
            result = _parse_llmfit_payload(payload, "x:1b")
            assert result.fit in (FitLevel.TOO_TIGHT, FitLevel.MARGINAL), (
                f"'{spelling}' should map to a problem state"
            )


class TestCheckModelFitLlmfitMissing:
    """Graceful handling when llmfit cannot be found or auto-installed."""

    def test_missing_llmfit_returns_unknown(self) -> None:
        with patch("codevet.preflight.ensure_llmfit", return_value=None):
            result = check_model_fit("qwen2.5-coder:7b", auto_install=False)
        assert result.fit == FitLevel.UNKNOWN
        assert result.llmfit_installed is False
        assert "not available" in result.reason.lower()


class TestCheckModelFitSubprocess:
    """Subprocess interaction with llmfit."""

    def _mock_proc(self, stdout: str = "", returncode: int = 0) -> MagicMock:
        proc = MagicMock()
        proc.stdout = stdout
        proc.stderr = ""
        proc.returncode = returncode
        return proc

    def test_successful_run_parses_json(self) -> None:
        payload = {
            "current": {"fit_level": "Good", "run_mode": "Gpu"},
            "recommended": {"ram_gb": 8.0},
        }
        with (
            patch("codevet.preflight.ensure_llmfit", return_value="/usr/bin/llmfit"),
            patch(
                "codevet.preflight.subprocess.run",
                return_value=self._mock_proc(stdout=json.dumps(payload)),
            ),
        ):
            result = check_model_fit("qwen2.5-coder:7b")
        assert result.fit == FitLevel.GOOD
        assert result.required_ram_gb == 8.0

    def test_llmfit_non_zero_exit_returns_unknown(self) -> None:
        with (
            patch("codevet.preflight.ensure_llmfit", return_value="/usr/bin/llmfit"),
            patch(
                "codevet.preflight.subprocess.run",
                return_value=self._mock_proc(returncode=1),
            ),
        ):
            result = check_model_fit("mystery:99b")
        assert result.fit == FitLevel.UNKNOWN
        assert "could not classify" in result.reason

    def test_llmfit_invalid_json_returns_unknown(self) -> None:
        with (
            patch("codevet.preflight.ensure_llmfit", return_value="/usr/bin/llmfit"),
            patch(
                "codevet.preflight.subprocess.run",
                return_value=self._mock_proc(stdout="not json at all"),
            ),
        ):
            result = check_model_fit("mystery:1b")
        assert result.fit == FitLevel.UNKNOWN
        assert "non-JSON" in result.reason

    def test_subprocess_timeout_is_handled(self) -> None:
        with (
            patch("codevet.preflight.ensure_llmfit", return_value="/usr/bin/llmfit"),
            patch(
                "codevet.preflight.subprocess.run",
                side_effect=subprocess.TimeoutExpired("llmfit", 30),
            ),
        ):
            result = check_model_fit("mystery:1b")
        assert result.fit == FitLevel.UNKNOWN
        assert "failed to execute" in result.reason


class TestEnsureLlmfit:
    """Auto-installer behavior."""

    def test_existing_installation_is_reused(self, tmp_path) -> None:
        """If llmfit is on PATH, we return it without downloading."""
        from codevet.preflight import ensure_llmfit

        with patch(
            "codevet.preflight._find_llmfit", return_value="/existing/llmfit"
        ):
            result = ensure_llmfit()
        assert result == "/existing/llmfit"

    def test_no_auto_install_returns_none_when_missing(self) -> None:
        from codevet.preflight import ensure_llmfit

        with patch("codevet.preflight._find_llmfit", return_value=None):
            result = ensure_llmfit(auto_install=False)
        assert result is None

    def test_unsupported_platform_returns_none(self) -> None:
        from codevet.preflight import ensure_llmfit

        with (
            patch("codevet.preflight._find_llmfit", return_value=None),
            patch("codevet.preflight._asset_for_host", return_value=None),
        ):
            result = ensure_llmfit()
        assert result is None


class TestAssetForHost:
    """Host → asset filename mapping."""

    def test_linux_x86_64_maps_to_gnu_tarball(self) -> None:
        from codevet.preflight import _asset_for_host

        with (
            patch("codevet.preflight.platform.system", return_value="Linux"),
            patch("codevet.preflight.platform.machine", return_value="x86_64"),
        ):
            asset = _asset_for_host("v0.9.2")
        assert asset == "llmfit-v0.9.2-x86_64-unknown-linux-gnu.tar.gz"

    def test_windows_amd64_maps_to_msvc_zip(self) -> None:
        from codevet.preflight import _asset_for_host

        with (
            patch("codevet.preflight.platform.system", return_value="Windows"),
            patch("codevet.preflight.platform.machine", return_value="AMD64"),
        ):
            asset = _asset_for_host("v0.9.2")
        assert asset == "llmfit-v0.9.2-x86_64-pc-windows-msvc.zip"

    def test_apple_silicon_maps_to_aarch64_darwin(self) -> None:
        from codevet.preflight import _asset_for_host

        with (
            patch("codevet.preflight.platform.system", return_value="Darwin"),
            patch("codevet.preflight.platform.machine", return_value="arm64"),
        ):
            asset = _asset_for_host("v0.9.2")
        assert asset == "llmfit-v0.9.2-aarch64-apple-darwin.tar.gz"

    def test_unknown_platform_returns_none(self) -> None:
        from codevet.preflight import _asset_for_host

        with (
            patch("codevet.preflight.platform.system", return_value="FreeBSD"),
            patch("codevet.preflight.platform.machine", return_value="sparc64"),
        ):
            asset = _asset_for_host("v0.9.2")
        assert asset is None


class TestFetchLatestVersion:
    """GitHub API version lookup with caching."""

    def test_returns_cached_version_when_fresh(self, tmp_path) -> None:
        from codevet.preflight import _fetch_latest_version

        cache_payload = {"version": "v1.2.3", "fetched_at": 9999999999.0}
        cache_file = tmp_path / "llmfit-version.json"
        cache_file.write_text(json.dumps(cache_payload), encoding="utf-8")

        with patch(
            "codevet.preflight._version_cache_path", return_value=cache_file
        ):
            version = _fetch_latest_version()
        assert version == "v1.2.3"

    def test_fallback_version_on_network_error(self, tmp_path) -> None:
        from urllib.error import URLError

        from codevet.preflight import LLMFIT_FALLBACK_VERSION, _fetch_latest_version

        missing_cache = tmp_path / "no-such-file.json"

        with (
            patch(
                "codevet.preflight._version_cache_path",
                return_value=missing_cache,
            ),
            patch(
                "codevet.preflight.urllib.request.urlopen",
                side_effect=URLError("offline"),
            ),
        ):
            version = _fetch_latest_version()
        assert version == LLMFIT_FALLBACK_VERSION

    def test_stale_cache_triggers_refetch(self, tmp_path) -> None:
        from codevet.preflight import _fetch_latest_version

        # Cache entry from 1970.
        stale = {"version": "v0.1.0", "fetched_at": 0.0}
        cache_file = tmp_path / "llmfit-version.json"
        cache_file.write_text(json.dumps(stale), encoding="utf-8")

        class _FakeResp:
            def read(self) -> bytes:
                return json.dumps({"tag_name": "v9.9.9"}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        with (
            patch(
                "codevet.preflight._version_cache_path", return_value=cache_file
            ),
            patch(
                "codevet.preflight.urllib.request.urlopen",
                return_value=_FakeResp(),
            ),
        ):
            version = _fetch_latest_version()
        assert version == "v9.9.9"


class TestFitResultProperties:
    """Domain properties of the FitResult dataclass."""

    @pytest.mark.parametrize(
        ("level", "block", "warn"),
        [
            (FitLevel.PERFECT, False, False),
            (FitLevel.GOOD, False, False),
            (FitLevel.MARGINAL, False, True),
            (FitLevel.TOO_TIGHT, True, False),
            (FitLevel.UNKNOWN, False, False),
        ],
    )
    def test_block_and_warn_flags(
        self, level: FitLevel, block: bool, warn: bool
    ) -> None:
        result = FitResult(fit=level, model="x:1b", reason="test")
        assert result.should_block is block
        assert result.should_warn is warn
