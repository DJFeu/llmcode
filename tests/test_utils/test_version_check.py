"""Tests for llm_code.utils.version_check."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.utils.version_check import VersionInfo, _parse_version, check_latest_version


# ---------------------------------------------------------------------------
# _parse_version helpers
# ---------------------------------------------------------------------------


class TestParseVersion:
    def test_plain_version(self):
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_v_prefix(self):
        assert _parse_version("v1.0.0") == (1, 0, 0)

    def test_single_component(self):
        assert _parse_version("5") == (5,)

    def test_invalid_returns_zero(self):
        assert _parse_version("invalid") == (0,)


# ---------------------------------------------------------------------------
# VersionInfo dataclass
# ---------------------------------------------------------------------------


class TestVersionInfo:
    def test_frozen(self):
        info = VersionInfo(current="0.1.0", latest="0.2.0", is_outdated=True, release_url="https://x")
        with pytest.raises((AttributeError, TypeError)):
            info.current = "0.3.0"  # type: ignore[misc]

    def test_not_outdated(self):
        info = VersionInfo(current="0.2.0", latest="0.2.0", is_outdated=False, release_url="")
        assert not info.is_outdated


# ---------------------------------------------------------------------------
# check_latest_version — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_latest_version_outdated():
    """Returns VersionInfo with is_outdated=True when remote is newer."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "tag_name": "v0.5.0",
        "html_url": "https://github.com/djfeu-adam/llm-code/releases/tag/v0.5.0",
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        info = await check_latest_version("0.1.0")

    assert info is not None
    assert info.is_outdated is True
    assert info.current == "0.1.0"
    assert info.latest == "0.5.0"
    assert "releases" in info.release_url


@pytest.mark.asyncio
async def test_check_latest_version_up_to_date():
    """Returns VersionInfo with is_outdated=False when versions match."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "tag_name": "v0.1.0",
        "html_url": "https://github.com/djfeu-adam/llm-code/releases/tag/v0.1.0",
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        info = await check_latest_version("0.1.0")

    assert info is not None
    assert info.is_outdated is False


@pytest.mark.asyncio
async def test_check_latest_version_newer_local():
    """Returns VersionInfo with is_outdated=False when local is newer."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "tag_name": "v0.0.9",
        "html_url": "",
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        info = await check_latest_version("0.1.0")

    assert info is not None
    assert info.is_outdated is False


# ---------------------------------------------------------------------------
# check_latest_version — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_latest_version_network_error():
    """Returns None on network failure."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("network error"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        info = await check_latest_version("0.1.0")

    assert info is None


@pytest.mark.asyncio
async def test_check_latest_version_malformed_response():
    """Returns None when JSON is missing expected keys."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"unexpected_key": "value"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        info = await check_latest_version("0.1.0")

    assert info is None


@pytest.mark.asyncio
async def test_check_latest_version_http_error():
    """Returns None on HTTP error (e.g. 404, 500)."""
    import httpx

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="Not Found",
        request=MagicMock(),
        response=MagicMock(),
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        info = await check_latest_version("0.1.0")

    assert info is None
