"""Free GitHub-based update checker for Nova Agent.

This module talks directly to the GitHub REST API and does not depend on
the agent's AI model or any paid service.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

OWNER = "the-sanjeevkumar"
REPO = "nova-agent"
API_LATEST = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{OWNER}/{REPO}/releases/latest"
RAW_INSTALL_URL = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/main/install.sh"


def _installed_version() -> str:
    try:
        from nova_cli.version_info import get_version_info

        info = get_version_info()
        v = getattr(info, "base_version", None) or getattr(info, "derived_version", None)
        if v and v != "unknown":
            return str(v)
    except Exception:
        pass
    try:
        from nova_cli import __version__

        v = str(__version__)
        if v and v != "0.0.0":
            return v
    except Exception:
        pass
    try:
        import tomllib
        from pathlib import Path

        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        return str(data.get("project", {}).get("version", "0.0.0"))
    except Exception:
        return "0.0.0"


def _parse_version(value: str) -> tuple[int, ...]:
    value = (value or "").strip().lstrip("vV")
    # Keep leading numeric components only.
    match = re.match(r"(\d+(?:\.\d+)*)", value)
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(installed: str, latest: str) -> bool:
    """Semantic-ish numeric comparison (no alphabetical string compare)."""
    a = _parse_version(installed)
    b = _parse_version(latest)
    # Pad to equal length.
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return b > a


def fetch_latest_release(timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(
        API_LATEST,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Nova-Agent-UpdateChecker",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def check_for_github_update() -> Dict[str, Any]:
    installed = _installed_version()
    result: Dict[str, Any] = {
        "installed": installed,
        "latest": None,
        "update_available": False,
        "release_notes": "",
        "html_url": RELEASES_PAGE,
        "download_url": None,
        "error": None,
    }
    data = fetch_latest_release()
    if data is None:
        result["error"] = "Could not reach GitHub."
        return result
    tag = str(data.get("tag_name") or "").strip()
    latest = tag.lstrip("vV") if tag else ""
    result["latest"] = latest or None
    result["release_notes"] = str(data.get("body") or "")
    result["html_url"] = str(data.get("html_url") or RELEASES_PAGE)
    assets = data.get("assets") or []
    if isinstance(assets, list) and assets:
        result["download_url"] = assets[0].get("browser_download_url")
    if latest and is_newer(installed, latest):
        result["update_available"] = True
    return result


def curl_update_command(os_name: str = "linux") -> str:
    return f"curl -fsSL {RAW_INSTALL_URL} -o update.sh && bash update.sh"
