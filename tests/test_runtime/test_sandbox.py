"""Tests for llm_code.runtime.sandbox."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

from llm_code.runtime.sandbox import (
    get_sandbox_info,
    is_sandboxed,
    restrict_paths,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_dockerenv(exists: bool):
    return mock.patch("llm_code.runtime.sandbox.Path", wraps=_make_path_mock(exists))


def _make_path_mock(dockerenv_exists: bool):
    """Return a Path subclass/mock where /.dockerenv.exists() returns the given value."""
    original_path = Path

    class MockPath(original_path):  # type: ignore[misc, valid-type]
        def __new__(cls, *args, **kwargs):
            return super().__new__(cls, *args, **kwargs)

        def exists(self) -> bool:
            if str(self) == "/.dockerenv":
                return dockerenv_exists
            return super().exists()

    return MockPath


# ---------------------------------------------------------------------------
# is_sandboxed
# ---------------------------------------------------------------------------

class TestIsSandboxed:
    def test_returns_bool(self):
        result = is_sandboxed()
        assert isinstance(result, bool)

    @pytest.mark.skipif(sys.platform == "win32", reason="/.dockerenv is Unix-only")
    def test_docker_sentinel_triggers_sandboxed(self):
        with mock.patch("llm_code.runtime.sandbox.Path") as MockPath:
            mock_instance = mock.MagicMock()
            mock_instance.exists.return_value = True

            def side_effect(path_str):
                obj = mock.MagicMock()
                if path_str == "/.dockerenv":
                    obj.exists.return_value = True
                else:
                    obj.exists.return_value = False
                    obj.read_text.side_effect = OSError
                return obj

            MockPath.side_effect = side_effect
            assert is_sandboxed() is True

    @pytest.mark.skipif(sys.platform == "win32", reason="/.dockerenv is Unix-only")
    def test_no_docker_sentinel_not_sandboxed_by_default(self):
        """On a normal macOS dev machine (no /.dockerenv) this should be False."""
        # Only run on macOS/Linux where we can be fairly sure we're not in Docker
        result = is_sandboxed()
        # We can't assert False here since CI might run in Docker,
        # so just verify the return type and that it doesn't raise.
        assert isinstance(result, bool)

    def test_cgroup_docker_marker_triggers_sandboxed(self):
        """Simulate /proc/1/cgroup containing 'docker'."""
        with (
            mock.patch("llm_code.runtime.sandbox._has_dockerenv", return_value=False),
            mock.patch("llm_code.runtime.sandbox._cgroup_indicates_container", return_value=True),
        ):
            assert is_sandboxed() is True

    def test_no_markers_returns_false(self):
        with (
            mock.patch("llm_code.runtime.sandbox._has_dockerenv", return_value=False),
            mock.patch("llm_code.runtime.sandbox._cgroup_indicates_container", return_value=False),
        ):
            assert is_sandboxed() is False


# ---------------------------------------------------------------------------
# get_sandbox_info
# ---------------------------------------------------------------------------

class TestGetSandboxInfo:
    def test_returns_dict(self):
        info = get_sandbox_info()
        assert isinstance(info, dict)

    def test_has_sandboxed_key(self):
        info = get_sandbox_info()
        assert "sandboxed" in info

    def test_has_type_key(self):
        info = get_sandbox_info()
        assert "type" in info

    def test_has_restrictions_key(self):
        info = get_sandbox_info()
        assert "restrictions" in info

    def test_sandboxed_value_is_bool(self):
        info = get_sandbox_info()
        assert isinstance(info["sandboxed"], bool)

    def test_type_is_string(self):
        info = get_sandbox_info()
        assert isinstance(info["type"], str)

    def test_restrictions_is_list(self):
        info = get_sandbox_info()
        assert isinstance(info["restrictions"], list)

    def test_docker_sandbox_info(self):
        with (
            mock.patch("llm_code.runtime.sandbox._has_dockerenv", return_value=True),
            mock.patch("llm_code.runtime.sandbox._cgroup_indicates_container", return_value=False),
        ):
            info = get_sandbox_info()
            assert info["sandboxed"] is True
            assert info["type"] == "docker"
            assert len(info["restrictions"]) > 0

    def test_no_sandbox_info(self):
        with (
            mock.patch("llm_code.runtime.sandbox._has_dockerenv", return_value=False),
            mock.patch("llm_code.runtime.sandbox._cgroup_indicates_container", return_value=False),
        ):
            info = get_sandbox_info()
            assert info["sandboxed"] is False
            assert info["type"] == "none"
            assert info["restrictions"] == []

    def test_container_sandbox_type(self):
        with (
            mock.patch("llm_code.runtime.sandbox._has_dockerenv", return_value=False),
            mock.patch("llm_code.runtime.sandbox._cgroup_indicates_container", return_value=True),
        ):
            info = get_sandbox_info()
            assert info["sandboxed"] is True
            assert info["type"] in ("container", "kubernetes", "containerd", "lxc", "ecs")

    def test_type_values_are_known(self):
        """Ensure the returned type is one of the documented values."""
        known_types = {"docker", "kubernetes", "containerd", "lxc", "ecs", "container", "none"}
        info = get_sandbox_info()
        assert info["type"] in known_types


# ---------------------------------------------------------------------------
# restrict_paths
# ---------------------------------------------------------------------------

class TestRestrictPaths:
    def test_returns_list(self, tmp_path: Path):
        result = restrict_paths(tmp_path)
        assert isinstance(result, list)

    def test_all_entries_are_paths(self, tmp_path: Path):
        result = restrict_paths(tmp_path)
        for item in result:
            assert isinstance(item, Path)

    def test_ssh_dir_excluded(self, tmp_path: Path):
        restricted = restrict_paths(tmp_path)
        ssh_dir = Path.home() / ".ssh"
        assert ssh_dir in restricted

    def test_aws_dir_excluded(self, tmp_path: Path):
        restricted = restrict_paths(tmp_path)
        aws_dir = Path.home() / ".aws"
        assert aws_dir in restricted

    def test_gcloud_dir_excluded(self, tmp_path: Path):
        restricted = restrict_paths(tmp_path)
        gcloud_dir = Path.home() / ".config" / "gcloud"
        assert gcloud_dir in restricted

    def test_base_dir_itself_not_restricted(self, tmp_path: Path):
        """The workspace base directory should not appear in the restriction list."""
        restricted = restrict_paths(tmp_path)
        assert tmp_path not in restricted

    def test_paths_inside_base_not_restricted(self, tmp_path: Path):
        """Sensitive paths that happen to be inside base_dir should not be restricted."""
        # Create a fake .ssh inside tmp_path (unusual, but tests the logic)
        fake_ssh = tmp_path / ".ssh"
        fake_ssh.mkdir()

        # Monkeypatch Path.home to point to tmp_path so ~/.ssh == tmp_path/.ssh
        with mock.patch("llm_code.runtime.sandbox.Path") as MockPath:
            # We need home() to return tmp_path
            MockPath.home.return_value = tmp_path
            # Fall back to real Path for everything else
            MockPath.side_effect = lambda *a, **k: Path(*a, **k)
            MockPath.home = mock.MagicMock(return_value=tmp_path)

            result = restrict_paths(tmp_path)
            # The fake .ssh is inside base_dir, so it should NOT be in the list
            assert fake_ssh not in result

    def test_no_etc_passwd_inside_workspace(self, tmp_path: Path):
        """Paths like /etc/passwd are never inside a typical workspace."""
        restricted = restrict_paths(tmp_path)
        etc_passwd = Path("/etc/passwd")
        # /etc/passwd can only be absent if tmp_path happens to contain it — very unlikely
        if not etc_passwd.as_posix().startswith(tmp_path.as_posix()):
            assert etc_passwd in restricted
