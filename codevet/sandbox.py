"""Docker sandbox for safe code execution."""
from __future__ import annotations

import contextlib
import io
import logging
import tarfile
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from docker.errors import DockerException, ImageNotFound, NotFound

from codevet.models import SandboxConfig, SandboxResult

logger = logging.getLogger(__name__)

# Custom image tag for codevet's sandbox (base + pytest preinstalled).
# Built once on first run, reused thereafter.
_CODEVET_SANDBOX_IMAGE = "codevet-sandbox:0.1.0"

_DOCKERFILE = b"""\
FROM python:3.11-slim
RUN pip install --no-cache-dir pytest==8.3.3 && \\
    useradd -u 1001 -m runner
USER runner
WORKDIR /workspace
"""

if TYPE_CHECKING:
    from docker import DockerClient
    from docker.models.containers import Container


class DockerSandboxError(Exception):
    """Raised when a sandbox operation fails.

    Covers Docker not running, permission errors, timeout issues,
    and any other container lifecycle failures.
    """


class Sandbox:
    """Docker sandbox manager for safe code execution.

    Creates isolated, read-only containers with no network access
    to run untrusted code and tests safely.
    """

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._client: DockerClient | None = None

    def _get_client(self) -> DockerClient:
        """Return a lazily-initialized Docker client.

        Raises:
            DockerSandboxError: If Docker is not running or inaccessible.
        """
        if self._client is not None:
            return self._client

        try:
            # Extend the HTTP pool timeout well past Docker Desktop's slow
            # cold-start on Windows + WSL2. Default is 60s which is too
            # tight for containers that run long test suites or large fix
            # iterations. Cap at 5 minutes.
            client = docker.from_env(timeout=300)
            client.ping()
        except (DockerException, ConnectionError) as exc:
            raise DockerSandboxError(
                "Docker is not running or is not accessible. "
                "Please start Docker and try again."
            ) from exc

        self._client = client
        return self._client

    def _ensure_image(self, client: DockerClient) -> None:
        """Ensure the configured image is available (pulling if needed).

        Raises:
            DockerSandboxError: If the image cannot be pulled.
        """
        try:
            client.images.get(self._config.image)
        except ImageNotFound:
            try:
                client.images.pull(self._config.image)
            except DockerException as exc:
                raise DockerSandboxError(
                    f"Failed to pull image '{self._config.image}': {exc}"
                ) from exc

    def _ensure_codevet_image(self, client: DockerClient) -> str:
        """Ensure the custom codevet-sandbox image exists (build once).

        The custom image is a thin layer over the configured base image
        with ``pytest`` preinstalled. Built on first run, cached on the
        host Docker daemon for all subsequent runs.

        Returns:
            The tag of the codevet-sandbox image to use for ``containers.run``.
        """
        try:
            client.images.get(_CODEVET_SANDBOX_IMAGE)
            return _CODEVET_SANDBOX_IMAGE
        except ImageNotFound:
            pass

        # Build a minimal image: base + pytest.
        self._ensure_image(client)
        dockerfile = _DOCKERFILE.replace(
            b"FROM python:3.11-slim",
            f"FROM {self._config.image}".encode(),
        )

        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            info = tarfile.TarInfo(name="Dockerfile")
            info.size = len(dockerfile)
            tar.addfile(info, io.BytesIO(dockerfile))
        tar_buffer.seek(0)

        try:
            client.images.build(
                fileobj=tar_buffer,
                custom_context=True,
                tag=_CODEVET_SANDBOX_IMAGE,
                rm=True,
            )
        except DockerException as exc:
            raise DockerSandboxError(
                f"Failed to build codevet-sandbox image: {exc}"
            ) from exc

        return _CODEVET_SANDBOX_IMAGE

    def run(self, code: str, test_code: str | None = None) -> SandboxResult:
        """Execute code (and optional tests) inside a sandboxed container.

        Args:
            code: Python source to evaluate.
            test_code: Optional pytest test source.  When provided the
                container runs ``python -m pytest`` instead of ``python``.

        Returns:
            A ``SandboxResult`` with captured output, timing, and exit status.
        """
        logger.info("[sandbox] Acquiring Docker client...")
        client = self._get_client()
        logger.info("[sandbox] Ensuring codevet-sandbox image...")
        image_tag = self._ensure_codevet_image(client)
        logger.info("[sandbox] Image ready: %s", image_tag)

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)

            # NOTE: avoid "code.py" — shadows Python's stdlib ``code`` module
            # and breaks ``from code import ...`` in generated tests.
            code_path = workspace / "solution.py"
            code_path.write_text(code, encoding="utf-8")

            if test_code is not None:
                test_path = workspace / "test_solution.py"
                test_path.write_text(test_code, encoding="utf-8")
                command = (
                    "python -m pytest test_solution.py -v --tb=short --no-header"
                )
            else:
                command = "python solution.py"

            logger.info("[sandbox] Running container: %s", command)
            result = self._run_container(client, workspace, command, image_tag)
            logger.info(
                "[sandbox] Container done: exit=%s, timed_out=%s, duration=%.1fs",
                result.exit_code,
                result.timed_out,
                result.duration_seconds,
            )
            return result

    def _run_container(
        self,
        client: DockerClient,
        workspace: Path,
        command: str,
        image_tag: str,
    ) -> SandboxResult:
        """Create, run, and clean up a sandboxed container.

        Returns:
            A ``SandboxResult`` populated from the container's execution.
        """
        container: Container | None = None
        start_time = time.monotonic()

        try:
            logger.info("[sandbox] containers.run() starting...")
            container = client.containers.run(
                image=image_tag,
                command=command,
                read_only=self._config.read_only,
                network_mode="none",
                security_opt=list(self._config.security_opt),
                tmpfs=dict(self._config.tmpfs_mounts),
                volumes={
                    str(workspace): {"bind": "/workspace", "mode": "ro"},
                },
                working_dir="/workspace",
                mem_limit=self._config.mem_limit,
                pids_limit=self._config.pids_limit,
                cap_drop=list(self._config.cap_drop),
                detach=True,
            )
            logger.info(
                "[sandbox] Container started: id=%s",
                container.id[:12] if container.id else "?",
            )

            try:
                logger.info(
                    "[sandbox] container.wait(timeout=%ds)...",
                    self._config.timeout_seconds,
                )
                result = container.wait(timeout=self._config.timeout_seconds)
                duration = time.monotonic() - start_time
                logger.info(
                    "[sandbox] container.wait() done in %.1fs, status=%s",
                    duration,
                    result.get("StatusCode"),
                )

                # Use demux=True to fetch stdout+stderr in a single API call.
                # This avoids two separate npipe round-trips that can hang.
                logger.info("[sandbox] container.logs() fetching output...")
                log_start = time.monotonic()
                try:
                    combined = container.logs(
                        stdout=True, stderr=True, stream=False, timestamps=False
                    )
                    # docker-py returns multiplexed bytes when both streams are
                    # requested; decode defensively.
                    raw = combined.decode("utf-8", errors="replace") if combined else ""
                    stdout_text = raw
                    stderr_text = ""
                except Exception as log_exc:
                    logger.warning(
                        "[sandbox] container.logs() failed: %s", log_exc
                    )
                    stdout_text = ""
                    stderr_text = f"Failed to read container logs: {log_exc}"
                logger.info(
                    "[sandbox] container.logs() done in %.1fs (%d bytes)",
                    time.monotonic() - log_start,
                    len(stdout_text) + len(stderr_text),
                )

                return SandboxResult(
                    exit_code=int(result.get("StatusCode", -1)),
                    stdout=stdout_text,
                    stderr=stderr_text,
                    timed_out=False,
                    duration_seconds=duration,
                )

            except Exception as exc:
                duration = time.monotonic() - start_time
                logger.warning(
                    "[sandbox] exception during wait/logs (%.1fs): %s",
                    duration,
                    exc,
                )

                # docker-py raises requests.exceptions.ConnectionError (or a
                # wrapped variant) on wait() timeout.  We also guard against
                # the generic DockerException for robustness.
                if _is_timeout(exc):
                    return SandboxResult(
                        exit_code=-1,
                        stdout="",
                        stderr=f"Container timed out after {self._config.timeout_seconds}s",
                        timed_out=True,
                        duration_seconds=duration,
                    )

                raise DockerSandboxError(
                    f"Container execution failed: {exc}"
                ) from exc

        finally:
            if container is not None:
                logger.info("[sandbox] Cleaning up container...")
                self._cleanup_container(container)
                logger.info("[sandbox] Cleanup done")

    @staticmethod
    def _cleanup_container(container: Container) -> None:
        """Force-remove a container, ignoring errors if already gone."""
        with contextlib.suppress(DockerException, NotFound):
            container.kill()

        with contextlib.suppress(DockerException, NotFound):
            container.remove(force=True)

    # -- context manager ------------------------------------------------

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None


def _is_timeout(exc: Exception) -> bool:
    """Heuristic: detect timeout-related exceptions from docker-py / urllib3."""
    timeout_indicators = ("timed out", "timeout", "read timeout", "connect timeout")
    message = str(exc).lower()
    return any(indicator in message for indicator in timeout_indicators)
