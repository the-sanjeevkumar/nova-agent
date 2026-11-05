"""Frozen (PyInstaller/binary) install self-update.

A binary install has no git tree: the update contract's admission gate would
refuse it (or, worse without this module, git plumbing would run against
whatever directory the process happened to start in). This module self-updates
by downloading the platform-matching release asset from GitHub Releases,
verifying its SHA-256 against the release's ``SHA256SUMS.txt``, and swapping the
running executable in place — backup first, rollback on failure.

User data never enters the swap: everything the agent persists lives under
``get_hermes_home()``, which this module never touches.
"""

from __future__ import annotations

import hashlib
import json
import platform as _platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = "the-sanjeevkumar/nova-agent"
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_USER_AGENT = "NovaAgent-FrozenUpdater"

# (os, arch) -> release asset containing the platform binary. The names are the
# CI artifact names from .github/workflows/release.yml (and its SHA256SUMS.txt
# entries) — never hardcode them anywhere else.
PLATFORM_ASSETS: dict[tuple[str, str], str] = {
    ("windows", "x86_64"): "nova-agent-windows-x86_64.zip",
    ("linux", "x86_64"): "nova-agent-linux-x86_64.tar.gz",
    ("macos", "x86_64"): "nova-agent-macos-x86_64.tar.gz",
    ("macos", "arm64"): "nova-agent-macos-arm64.tar.gz",
}

_CHECKSUM_LIST = "SHA256SUMS.txt"


class FrozenUpdateError(RuntimeError):
    """A frozen-install update failed after verification and was rolled back."""


def is_frozen() -> bool:
    """True when running from a PyInstaller (or any single-file binary) build."""
    return bool(getattr(sys, "frozen", False))


def platform_key(platform: str | None = None, machine: str | None = None) -> tuple[str, str] | None:
    """Normalize (sys.platform, platform.machine()) to an OS/arch pair, or None if unsupported."""
    platform = (platform if platform is not None else sys.platform).lower()
    machine = (machine if machine is not None else _platform.machine()).lower()
    if machine in ("amd64", "x86_64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        return None
    if platform.startswith("win"):
        os_name = "windows"
    elif platform.startswith("darwin"):
        os_name = "macos"
    elif platform.startswith("linux"):
        os_name = "linux"
    else:
        return None
    if (os_name, arch) not in PLATFORM_ASSETS:
        return None
    return (os_name, arch)


def pick_asset_name(assets: list[str], platform: str | None = None, machine: str | None = None) -> str | None:
    """Return the release asset to download for this platform, or None if absent."""
    key = platform_key(platform, machine)
    if key is None:
        return None
    wanted = PLATFORM_ASSETS[key]
    return wanted if wanted in assets else None


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse a ``sha256sum`` output file: '<hex>  <name>' per line."""
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, name = line.partition(" ")
        digest = digest.strip()
        name = name.lstrip(" *").strip()
        if len(digest) != 64 or not name:
            continue
        try:
            int(digest, 16)
        except ValueError:
            continue
        checksums[name] = digest.lower()
    return checksums


def verify_sha256(path: Path, expected: str) -> bool:
    """True when the file's SHA-256 matches ``expected`` (any casing)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected.lower()


def _extract_binary(payload: Path, wanted: str, stage: Path) -> Path:
    """Extract exactly the ``wanted`` member (nova / nova.exe) from a release payload."""
    stage.mkdir(parents=True, exist_ok=True)
    if wanted.endswith(".zip"):
        with zipfile.ZipFile(payload) as archive:
            member = next(name for name in archive.namelist() if Path(name).name == "nova.exe")
            archive.extract(member, stage)
    else:
        import tarfile

        with tarfile.open(payload, "r:*") as archive:
            member = next(tid for tid in archive.getmembers() if Path(tid.name).name == "nova")
            archive.extract(member, stage, filter="data")
    extracted = stage / ("nova.exe" if wanted.endswith(".zip") else "nova")
    if not extracted.is_file():
        raise FrozenUpdateError(f"{wanted} did not contain a nova binary")
    return extracted


@dataclass(frozen=True)
class _ReleaseAsset:
    name: str
    url: str


@dataclass(frozen=True)
class _Release:
    tag: str
    assets: list[_ReleaseAsset]

    def checksum_url(self) -> str | None:
        for asset in self.assets:
            if asset.name == _CHECKSUM_LIST:
                return asset.url
        return None


def _fetch_latest_release(timeout: float = 30.0) -> _Release:
    request = urllib.request.Request(
        RELEASE_API, headers={"Accept": "application/vnd.github.v3+json", "User-Agent": _USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode())
    return _Release(
        tag=data.get("tag_name") or "",
        assets=[_ReleaseAsset(name=a["name"], url=a["browser_download_url"])
                for a in data.get("assets", {}) if isinstance(a, dict) and a.get("name")],
    )


def _download(url: str, dest: Path, timeout: float = 120.0) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(dest, "wb") as fh:
        shutil.copyfileobj(response, fh)
    return dest


def _verify_or_rollback(payload: Path, expected: str | None) -> None:
    """Fail cold before any swap when the checksum is missing or mismatched."""
    if not expected:
        raise FrozenUpdateError(f"Release's {_CHECKSUM_LIST} has no entry for the download — refusing ")
    if not verify_sha256(payload, expected):
        raise FrozenUpdateError("Downloaded update failed SHA-256 verification — refusing")


def _swap_binary(new_binary: Path, target: Path, is_windows: bool) -> None:
    """Replace the running executable; returns after the swap lands.

    Windows: a running onefile exe cannot be deleted or overwritten, but it can
    be renamed — the whole swap trick. POSIX: an in-use binary can be replaced
    atomically with os.replace().
    """
    backup = target.with_name(target.name + ".old")
    if is_windows:
        if backup.exists():
            backup.unlink()
        target.rename(backup)
        try:
            shutil.copyfile(new_binary, target)
        except Exception:
            if backup.exists():
                shutil.copyfile(backup, target)
            raise
    else:
        shutil.copy2(target, backup)
        try:
            staging = target.with_name(target.name + f".new-{target.stat().st_ino}")
            shutil.copyfile(new_binary, staging)
            staging.chmod(0o755)
            staging.replace(target)
        except Exception:
            shutil.copy2(backup, target)
            raise


def _apply_frozen(payload: Path, expected: str, target: Path, *, is_windows: bool) -> None:
    """Verify the downloaded payload FIRST (fail cold, never touch the install),
    then extract and swap ``target`` with rollback."""
    _verify_or_rollback(payload, expected)
    with tempfile.TemporaryDirectory(prefix="nova-update-") as tmp:
        stage = Path(tmp) / "stage"
        extracted = _extract_binary(payload, payload.name, stage)
        _swap_binary(extracted, target, is_windows)


def _updates_disabled() -> bool:
    try:
        from nova_cli.config import load_config

        return (load_config().get("updates") or {}).get("check") is False
    except Exception:
        return False


def startup_notice_for_frozen() -> str | None:
    """One-line available-update notice for binary installs (passive, cached)."""
    if not is_frozen() or _updates_disabled():
        return None
    try:
        from nova_cli.updater.update_checker import check_for_updates

        info = check_for_updates(force=False)
        if info.available:
            return (f"Update available: v{info.current_version} -> v{info.latest_version} "
                    f"- run 'nova update' to install it")
    except Exception:
        pass
    return None


def cmd_update_frozen(*, check_only: bool, yes: bool) -> None:
    """CLI entry for ``nova update`` on a frozen install. Exits the process."""
    current = _current_version()
    if check_only:
        from nova_cli.updater.update_checker import check_for_updates

        info = check_for_updates(force=True)
        if info.available:
            print(f"Update available: v{current} -> v{info.latest_version} - run 'nova update' to install")
            print(info.release_url or "")
            sys.exit(1)
        print(f"Up to date (v{current})")
        sys.exit(0)
    _run_update(yes=yes)


def _current_version() -> str:
    try:
        from nova_cli.version_info import get_version_info

        return get_version_info().base_version
    except Exception:
        return "unknown"


def _run_update(*, yes: bool, target: Path | None = None, is_windows: bool | None = None) -> None:
    target = target or Path(sys.executable)
    is_windows = sys.platform.startswith("win") if is_windows is None else is_windows
    key = platform_key()
    if key is None:
        print("No prebuilt update package for this platform — download one from the releases page for your OS and arch.")
        sys.exit(1)

    release = _fetch_latest_release()
    asset_name = pick_asset_name([a.name for a in release.assets])
    if asset_name is None:
        print(f"Release {release.tag or ('latest')} does not carry a binary for {key[0]}/{key[1]}. "
              "Download from the releases page instead.")
        sys.exit(1)
    asset = next(a for a in release.assets if a.name == asset_name)
    checksums_url = release.checksum_url()
    current = _current_version()
    tag_version = release.tag.lstrip("v")
    if tag_version and tag_version == current:
        print(f"Up to date (v{current})")
        sys.exit(0)

    if not yes:
        try:
            answer = input(f"Install Nova Agent v{tag_version} over v{current}? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Update cancelled.")
            sys.exit(0)

    tmp = Path(tempfile.mkdtemp(prefix="nova-frozen-"))
    try:
        payload = _download(asset.url, tmp / asset_name)
        expected = None
        if checksums_url:
            try:
                checksums = _download(checksums_url, tmp / _CHECKSUM_LIST)
                expected = parse_sha256sums(checksums.read_text()).get(asset_name)
            except Exception:
                expected = None
        _apply_frozen(payload, expected, target, is_windows=is_windows)
    except FrozenUpdateError as exc:
        print(f"Update failed: {exc}")
        print("Your Nova installation is unchanged.")
        sys.exit(1)
    except Exception as exc:
        print(f"Update failed: {exc}")
        sys.exit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"Updated to v{tag_version} (was v{current}). Restart Nova to run the new version.")
    sys.exit(0)
