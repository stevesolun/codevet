"""Tests for Docker sandbox."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from codevet.models import SandboxConfig

if TYPE_CHECKING:
    from pathlib import Path


class TestContainerCreation:
    """Verify Docker container is created with correct security options."""

    @pytest.fixture()
    def sandbox_config(self, tmp_path: Path) -> SandboxConfig:
        return SandboxConfig(project_dir=tmp_path)

    @pytest.fixture()
    def mock_container(self) -> MagicMock:
        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.return_value = b"All tests passed"
        return container

    @pytest.fixture()
    def mock_docker(self, mock_container: MagicMock):
        with patch("codevet.sandbox.docker") as mock_mod:
            client = MagicMock()
            mock_mod.from_env.return_value = client
            client.containers.run.return_value = mock_container
            yield client

    def test_container_created_with_security_opts(
        self, mock_docker: MagicMock, sandbox_config: SandboxConfig
    ):
        """Verify containers.run called with security_opt=["no-new-privileges"]."""
        from codevet.sandbox import Sandbox

        with Sandbox(sandbox_config) as sb:
            sb.run("print(1)", "def test_x(): assert True")

        call_kwargs = mock_docker.containers.run.call_args
        assert call_kwargs is not None
        _, kwargs = call_kwargs
        assert "no-new-privileges" in kwargs.get("security_opt", [])

    def test_container_readonly_filesystem(
        self, mock_docker: MagicMock, sandbox_config: SandboxConfig
    ):
        """Verify read_only=True passed to containers.run."""
        from codevet.sandbox import Sandbox

        with Sandbox(sandbox_config) as sb:
            sb.run("print(1)", "def test_x(): assert True")

        _, kwargs = mock_docker.containers.run.call_args
        assert kwargs.get("read_only") is True

    def test_container_no_network(
        self, mock_docker: MagicMock, sandbox_config: SandboxConfig
    ):
        """Verify network_mode="none"."""
        from codevet.sandbox import Sandbox

        with Sandbox(sandbox_config) as sb:
            sb.run("print(1)", "def test_x(): assert True")

        _, kwargs = mock_docker.containers.run.call_args
        assert kwargs.get("network_mode") == "none"

    def test_container_tmpfs_mounted(
        self, mock_docker: MagicMock, sandbox_config: SandboxConfig
    ):
        """Verify tmpfs={"/tmp": "size=100m"}."""
        from codevet.sandbox import Sandbox

        with Sandbox(sandbox_config) as sb:
            sb.run("print(1)", "def test_x(): assert True")

        _, kwargs = mock_docker.containers.run.call_args
        tmpfs = kwargs.get("tmpfs", {})
        assert "/tmp" in tmpfs
        assert "100m" in tmpfs["/tmp"]

    def test_project_dir_mounted_readonly(
        self, mock_docker: MagicMock, sandbox_config: SandboxConfig
    ):
        """Verify volumes dict has a directory mounted read-only into /workspace."""
        from codevet.sandbox import Sandbox

        with Sandbox(sandbox_config) as sb:
            sb.run("print(1)", "def test_x(): assert True")

        _, kwargs = mock_docker.containers.run.call_args
        volumes = kwargs.get("volumes", {})
        # sandbox.py mounts a temp workspace dir (not project_dir) as the volume
        assert len(volumes) > 0
        # At least one volume must be mounted read-only bound to /workspace
        ro_mounts = [
            v for v in volumes.values()
            if v.get("mode") == "ro" and v.get("bind") == "/workspace"
        ]
        assert len(ro_mounts) > 0


class TestContainerTimeout:
    """Verify timeout handling."""

    def test_container_timeout_kills(self, tmp_path: Path):
        """Mock container that times out, verify result.timed_out is True."""
        from codevet.sandbox import Sandbox

        config = SandboxConfig(project_dir=tmp_path, timeout_seconds=1)

        container = MagicMock()
        container.wait.side_effect = Exception("timed out")
        container.logs.return_value = b""

        with patch("codevet.sandbox.docker") as mock_mod:
            client = MagicMock()
            mock_mod.from_env.return_value = client
            client.containers.run.return_value = container

            with Sandbox(config) as sb:
                result = sb.run("import time; time.sleep(999)", "def test_x(): pass")

        assert result.timed_out is True


class TestContainerCleanup:
    """Verify container cleanup."""

    def test_container_cleanup_on_exit(self, tmp_path: Path):
        """Verify container.remove(force=True) called in finally/cleanup."""
        from codevet.sandbox import Sandbox

        config = SandboxConfig(project_dir=tmp_path)

        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.return_value = b""

        with patch("codevet.sandbox.docker") as mock_mod:
            client = MagicMock()
            mock_mod.from_env.return_value = client
            client.containers.run.return_value = container

            with Sandbox(config) as sb:
                sb.run("pass", "def test_x(): assert True")

        container.remove.assert_called_once_with(force=True)


class TestDockerErrors:
    """Verify Docker error handling."""

    def test_docker_not_running_error(self, tmp_path: Path):
        """Mock docker.from_env() raising ConnectionError, verify DockerSandboxError."""
        from codevet.sandbox import DockerSandboxError, Sandbox

        config = SandboxConfig(project_dir=tmp_path)

        with patch("codevet.sandbox.docker") as mock_mod:
            mock_mod.from_env.side_effect = ConnectionError("Docker not running")

            with pytest.raises(DockerSandboxError), Sandbox(config) as sb:
                sb.run("pass", "def test_x(): pass")


class TestEnsureImagePullFailure:
    """Verify _ensure_image raises when pull fails."""

    def test_ensure_image_pull_failure(self, tmp_path: Path):
        """When image not found locally and pull fails, DockerSandboxError is raised."""
        from docker.errors import DockerException, ImageNotFound

        from codevet.sandbox import DockerSandboxError, Sandbox

        config = SandboxConfig(project_dir=tmp_path)

        with patch("codevet.sandbox.docker") as mock_mod:
            client = MagicMock()
            mock_mod.from_env.return_value = client
            # ImageNotFound on get, then DockerException on pull
            client.images.get.side_effect = ImageNotFound("not found")
            client.images.pull.side_effect = DockerException("pull failed")

            with pytest.raises(DockerSandboxError, match="Failed to pull image"):
                with Sandbox(config) as sb:
                    sb.run("pass", "def test_x(): pass")


class TestSandboxRunCodeOnly:
    """Test sandbox.run with code only (no test_code)."""

    def test_run_code_only(self, tmp_path: Path):
        """When test_code is None, sandbox runs 'python solution.py'."""
        from codevet.sandbox import Sandbox

        config = SandboxConfig(project_dir=tmp_path)

        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.return_value = b"hello output"

        with patch("codevet.sandbox.docker") as mock_mod:
            client = MagicMock()
            mock_mod.from_env.return_value = client
            client.containers.run.return_value = container

            with Sandbox(config) as sb:
                result = sb.run("print('hello')")

        assert result.exit_code == 0
        _, kwargs = client.containers.run.call_args
        assert "python solution.py" in kwargs.get("command", "")


class TestContainerExecutionFailure:
    """Test non-timeout container execution failure."""

    def test_container_execution_error(self, tmp_path: Path):
        """When container.wait raises a non-timeout exception, DockerSandboxError is raised."""
        from codevet.sandbox import DockerSandboxError, Sandbox

        config = SandboxConfig(project_dir=tmp_path)

        container = MagicMock()
        container.wait.side_effect = Exception("something completely different")
        container.logs.return_value = b""

        with patch("codevet.sandbox.docker") as mock_mod:
            client = MagicMock()
            mock_mod.from_env.return_value = client
            client.containers.run.return_value = container

            with pytest.raises(DockerSandboxError, match="Container execution failed"):
                with Sandbox(config) as sb:
                    sb.run("print(1)", "def test_x(): assert True")
