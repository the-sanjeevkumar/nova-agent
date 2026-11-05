"""Behaviour contracts for the frozen (PyInstaller/binary) install self-update.

Covers the platform/asset mapping, the SHA256SUMS parsing + verification, the
payload extraction, and the swap stage (backup + rollback). The network and the
running-binary path are exercised only by the real release flow; these tests
drive the pure/staged parts directly.
"""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest

from nova_cli.frozen_update import (
    FrozenUpdateError,
    _apply_frozen,
    _extract_binary,
    _swap_binary,
    is_frozen,
    parse_sha256sums,
    pick_asset_name,
    platform_key,
    verify_sha256,
)


# ---------------------------------------------------------------- platform mapping

@pytest.mark.parametrize("platform,machine,expected", [
    ("win32", "AMD64", ("windows", "x86_64")),
    ("linux", "x86_64", ("linux", "x86_64")),
    ("darwin", "arm64", ("macos", "arm64")),
    ("darwin", "x86_64", ("macos", "x86_64")),
])
def test_platform_key_normalizes_vendor_names(platform, machine, expected) -> None:
    assert platform_key(platform, machine) == expected


@pytest.mark.parametrize("platform,machine", [
    ("linux", "armv7l"),      # no prebuilt asset
    ("win32", "arm64"),       # no windows-arm build
    ("freebsd13", "amd64"),   # unsupported OS
])
def test_platform_key_returns_none_without_a_prebuilt(platform, machine) -> None:
    assert platform_key(platform, machine) is None


def test_pick_asset_matches_ci_artifact_names() -> None:
    assets = ["nova-agent-linux-x86_64.tar.gz", "nova-agent-windows-x86_64.zip", "SHA256SUMS.txt"]
    assert pick_asset_name(assets, "win32", "AMD64") == "nova-agent-windows-x86_64.zip"
    assert pick_asset_name(assets, "linux", "x86_64") == "nova-agent-linux-x86_64.tar.gz"


def test_pick_asset_is_none_when_release_lacks_the_platform() -> None:
    assert pick_asset_name(["SHA256SUMS.txt"], "darwin", "arm64") is None


def test_is_frozen_defaults_to_false_in_source() -> None:
    assert is_frozen() is bool(getattr(__import__("sys"), "frozen", False))


# ---------------------------------------------------------------- checksum handling

def test_parse_sha256sums_reads_standard_output() -> None:
    digest = hashlib.sha256(b"hello").hexdigest()
    text = f"{digest}  nova-agent-linux-x86_64.tar.gz\n\n{digest.upper()} *nova-agent-windows-x86_64.zip\n"
    parsed = parse_sha256sums(text)
    assert parsed == {
        "nova-agent-linux-x86_64.tar.gz": digest,
        "nova-agent-windows-x86_64.zip": digest,
    }


def test_parse_sha256sums_drops_garbage_lines() -> None:
    assert parse_sha256sums("deadbeef short.txt  \nnonsense line\n") == {}


def test_verify_sha256_matches_and_rejects(tmp_path: Path) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"nova payload")
    assert verify_sha256(path, hashlib.sha256(b"nova payload").hexdigest())
    assert not verify_sha256(path, hashlib.sha256(b"other").hexdigest())


# ---------------------------------------------------------------- extraction

def _make_tar(tmp_path: Path, name: bytes = b"nova", content: bytes = b"NEW-BINARY") -> Path:
    payload = tmp_path / "nova-agent-linux-x86_64.tar.gz"
    member = tmp_path / name.decode()
    member.write_bytes(content)
    with tarfile.open(payload, "w:gz") as archive:
        archive.add(member, arcname=name.decode())
    return payload


def _make_zip(tmp_path: Path, content: bytes = b"NEW-EXE") -> Path:
    payload = tmp_path / "nova-agent-windows-x86_64.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("nova.exe", content)
    return payload


def test_extract_binary_pulls_only_the_nova_member(tmp_path: Path) -> None:
    payload = _make_zip(tmp_path)
    stage = tmp_path / "stage"
    extracted = _extract_binary(payload, payload.name, stage)
    assert extracted == stage / "nova.exe"
    assert extracted.read_bytes() == b"NEW-EXE"


def test_extract_binary_reads_posix_tarball(tmp_path: Path) -> None:
    payload = _make_tar(tmp_path)
    stage = tmp_path / "stage"
    extracted = _extract_binary(payload, payload.name, stage)
    assert extracted == stage / "nova"
    assert extracted.read_bytes() == b"NEW-BINARY"


def test_extract_binary_fails_without_a_nova_member(tmp_path: Path) -> None:
    payload = tmp_path / "empty.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("other.txt", "")
    with pytest.raises(StopIteration):
        _extract_binary(payload, payload.name, tmp_path / "stage")


# ---------------------------------------------------------------- swap + rollback

def test_swap_binary_replaces_target_and_keeps_backup_posix(tmp_path: Path) -> None:
    target = tmp_path / "nova"
    target.write_bytes(b"OLD")
    target.chmod(0o755)
    new_binary = tmp_path / "staged-nova"
    new_binary.write_bytes(b"NEW")

    _swap_binary(new_binary, target, is_windows=False)

    assert target.read_bytes() == b"NEW"
    assert (target.stat().st_mode & 0o111) == 0o111
    assert target.with_name("nova.old").read_bytes() == b"OLD"


def test_apply_frozen_verifies_before_touching_the_install(tmp_path: Path) -> None:
    payload = _make_tar(tmp_path, content=b"NEW")
    target = tmp_path / "nova"
    target.write_bytes(b"OLD")

    with pytest.raises(FrozenUpdateError):
        _apply_frozen(payload, hashlib.sha256(b"WRONG").hexdigest(), target, is_windows=False)

    # fail-cold: the running binary is untouched
    assert target.read_bytes() == b"OLD"


def test_apply_frozen_swaps_a_verified_payload(tmp_path: Path) -> None:
    payload = _make_tar(tmp_path, content=b"NEW")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    target = tmp_path / "nova"
    target.write_bytes(b"OLD")

    _apply_frozen(payload, digest, target, is_windows=False)

    assert target.read_bytes() == b"NEW"
    assert target.with_name("nova.old").read_bytes() == b"OLD"


def test_apply_frozen_rolls_back_when_the_swap_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _make_tar(tmp_path, content=b"NEW")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    target = tmp_path / "nova"
    target.write_bytes(b"OLD")

    real_copyfile = shutil.copyfile

    def failing_copyfile(src, dst, **kwargs):
        # only the staging copy into the live target fails; the backup copy succeeded
        if ".new" in Path(str(dst)).name:
            raise PermissionError("blocked")
        return real_copyfile(src, dst, **kwargs)

    monkeypatch.setattr(shutil, "copyfile", failing_copyfile)

    with pytest.raises(PermissionError):
        _apply_frozen(payload, digest, target, is_windows=False)

    # rollback restored the previous binary; the backup copy still exists
    assert target.read_bytes() == b"OLD"


def test_user_home_is_untouched_by_the_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home" / ".nova"
    home.mkdir(parents=True)
    (home / "memory").mkdir()
    (home / "memory" / "MEMORY.md").write_text("make the forge sing")
    monkeypatch.setenv("HERMES_HOME", str(home))

    payload = _make_tar(tmp_path, content=b"NEW")
    _apply_frozen(payload, hashlib.sha256(payload.read_bytes()).hexdigest(), tmp_path / "nova", is_windows=False)

    assert (home / "memory" / "MEMORY.md").read_text() == "make the forge sing"
