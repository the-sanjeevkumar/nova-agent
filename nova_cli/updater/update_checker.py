"""
Auto-update checker for Nova Agent.
Polls GitHub releases on startup and notifies if a new version is available.
"""

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from nova_cli.version_info import get_version_info

CACHE_FILE = Path.home() / '.nova' / 'update_cache.json'
CACHE_TTL = 24 * 60 * 60  # 24 hours
REPO = 'the-sanjeevkumar/nova-agent'
RELEASE_API = f'https://api.github.com/repos/{REPO}/releases/latest'


@dataclass
class UpdateInfo:
    available: bool
    current_version: str
    latest_version: Optional[str] = None
    release_url: Optional[str] = None
    release_notes: Optional[str] = None


def _read_cache() -> Optional[dict]:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text())
    except Exception:
        pass
    return None


def _write_cache(data: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data))
    except Exception:
        pass


def _is_cache_valid(cache: dict) -> bool:
    if 'timestamp' not in cache:
        return False
    return time.time() - cache['timestamp'] < CACHE_TTL


def check_for_updates(force: bool = False) -> UpdateInfo:
    """
    Check for available updates.
    Uses cached result unless force=True or cache expired.
    """
    current = get_version_info().base_version

    # Check cache first
    if not force:
        cache = _read_cache()
        if cache and _is_cache_valid(cache):
            latest = cache.get('latest_version', '')
            if latest and latest != current:
                return UpdateInfo(
                    available=True,
                    current_version=current,
                    latest_version=latest,
                    release_url=cache.get('release_url'),
                    release_notes=cache.get('release_notes'),
                )
            return UpdateInfo(available=False, current_version=current)

    # Fetch from GitHub
    try:
        req = urllib.request.Request(
            RELEASE_API,
            headers={'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'NovaAgent-Updater'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())

        latest = data.get('tag_name', '').lstrip('v')
        release_url = data.get('html_url')
        release_notes = data.get('body', '')

        # Update cache
        _write_cache({
            'timestamp': time.time(),
            'latest_version': latest,
            'release_url': release_url,
            'release_notes': release_notes,
        })

        if latest and latest != current:
            return UpdateInfo(
                available=True,
                current_version=current,
                latest_version=latest,
                release_url=release_url,
                release_notes=release_notes,
            )

    except Exception:
        # Silently fail - don't break startup on network issues
        pass

    return UpdateInfo(available=False, current_version=current)


def format_update_notification(info: UpdateInfo) -> str:
    """Format a user-friendly update notification."""
    if not info.available:
        return ""

    lines = [
        "",
        "╭─────────────────────────────────────────────────────────╮",
        "│  🌌 Nova Agent Update Available                         │",
        "╰─────────────────────────────────────────────────────────╯",
        f"   Current version: {info.current_version}",
        f"   Latest version:  {info.latest_version}",
        "",
        "   Run `nova update` to install the latest version.",
    ]

    if info.release_url:
        lines.append(f"   Release notes: {info.release_url}")

    lines.append("")
    return "\n".join(lines)


def maybe_show_update_notification() -> None:
    """
    Check for updates and print notification if available.
    Safe to call on every CLI startup.
    """
    # Skip in non-interactive contexts
    if os.environ.get('HERMES_NONINTERACTIVE', '').lower() in {'1', 'true', 'yes', 'on'}:
        return
    if os.environ.get('NOVA_NO_UPDATE_CHECK', '').lower() in {'1', 'true', 'yes', 'on'}:
        return

    info = check_for_updates()
    if info.available:
        print(format_update_notification(info))