"""Non-blocking version check against GitHub releases API."""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_RELEASES_URL = "https://api.github.com/repos/djfeu-adam/llm-code/releases/latest"
_TIMEOUT = 5.0


@dataclasses.dataclass(frozen=True)
class VersionInfo:
    current: str
    latest: str
    is_outdated: bool
    release_url: str


def _parse_version(tag: str) -> tuple[int, ...]:
    """Parse a version tag like 'v1.2.3' or '1.2.3' into a comparable tuple."""
    cleaned = tag.lstrip("v")
    try:
        return tuple(int(x) for x in cleaned.split("."))
    except ValueError:
        return (0,)


async def check_latest_version(current: str) -> VersionInfo | None:
    """Fetch latest release from GitHub and compare with *current*.

    Returns a :class:`VersionInfo` or ``None`` on any network/parse failure.
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                _RELEASES_URL,
                headers={"Accept": "application/vnd.github+json"},
                follow_redirects=True,
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        return None

    try:
        tag_name: str = data["tag_name"]
        release_url: str = data.get("html_url", "")
    except (KeyError, TypeError):
        return None

    is_outdated = _parse_version(tag_name) > _parse_version(current)
    return VersionInfo(
        current=current,
        latest=tag_name.lstrip("v"),
        is_outdated=is_outdated,
        release_url=release_url,
    )
