#!/usr/bin/env python3
"""Install the latest File Tools release into a plugin-local virtualenv."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence


LATEST_RELEASE_API = (
    "https://api.github.com/repos/krkawzq/file-tools/releases/latest"
)
RELEASE_DOWNLOAD_PREFIX = (
    "https://github.com/krkawzq/file-tools/releases/download/"
)
FASTMCP_REQUIREMENT = "fastmcp>=3.4.4"
SOCKSIO_REQUIREMENT = "socksio==1.*"


class InstallError(RuntimeError):
    """A user-actionable installation failure."""


def _plugin_root_from_script() -> Path:
    return Path(__file__).resolve().parents[3]


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _normalize_machine(machine: str) -> str:
    value = machine.lower().replace("-", "_")
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
    }
    return aliases.get(value, value)


def _version_tuple(value: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)", value)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _detect_linux_libc() -> tuple[str, str]:
    name, version = platform.libc_ver()
    lowered = name.lower()
    if "musl" in lowered:
        return "musl", version
    if "glibc" in lowered or "gnu" in lowered:
        return "glibc", version

    config_values = " ".join(
        str(sysconfig.get_config_var(key) or "")
        for key in ("HOST_GNU_TYPE", "MULTIARCH", "SOABI")
    ).lower()
    if "musl" in config_values:
        family = "musl"
    elif "gnu" in config_values or "glibc" in config_values:
        family = "glibc"
    else:
        family = ""

    try:
        result = subprocess.run(
            ["ldd", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = f"{result.stdout}\n{result.stderr}".lower()
    except (OSError, subprocess.SubprocessError):
        output = ""

    if "musl" in output:
        match = re.search(r"version\s+(\d+\.\d+)", output)
        return "musl", match.group(1) if match else version
    if "glibc" in output or "gnu libc" in output:
        versions = re.findall(r"\b(\d+\.\d+)\b", output)
        return "glibc", versions[-1] if versions else version
    return family, version


def _linux_wheel_floor(name: str, libc: str) -> tuple[int, int] | None:
    family = "musllinux" if libc == "musl" else "manylinux"
    match = re.search(rf"{family}_(\d+)_(\d+)", name)
    if match:
        return int(match.group(1)), int(match.group(2))
    if family == "manylinux":
        legacy = {
            "manylinux1": (2, 5),
            "manylinux2010": (2, 12),
            "manylinux2014": (2, 17),
        }
        for tag, floor in legacy.items():
            if tag in name:
                return floor
    return None


def _wheel_platform_score(
    filename: str,
    system: str,
    machine: str,
    libc: str = "",
    libc_version: str = "",
    os_version: str = "",
) -> int:
    name = filename.lower()
    system = system.lower()
    machine = _normalize_machine(machine)

    if not name.endswith(".whl") or not (
        name.startswith("mcp_file_tools-") or name.startswith("file_tools-")
    ):
        return -1
    if "-cp312-abi3-" not in name:
        return -1

    if system == "linux":
        if not any(tag in name for tag in ("manylinux", "musllinux", "-linux_")):
            return -1
        if machine not in name:
            return -1
        if "musllinux" in name:
            if libc != "musl":
                return -1
            floor = _linux_wheel_floor(name, libc)
            host = _version_tuple(libc_version)
            if floor and host and floor > host:
                return -1
            if floor and host:
                return 1000 + floor[0] * 100 + floor[1]
            return 500 - (floor[0] * 100 + floor[1]) if floor else 100
        if "manylinux" in name:
            if libc != "glibc":
                return -1
            floor = _linux_wheel_floor(name, "glibc")
            host = _version_tuple(libc_version)
            if floor and host and floor > host:
                return -1
            if floor and host:
                return 1000 + floor[0] * 100 + floor[1]
            # Without a detected host version, prefer the oldest/safest floor.
            return 500 - (floor[0] * 100 + floor[1]) if floor else 100
        return 10

    if system == "darwin":
        if "macosx" not in name:
            return -1
        mac_machine = "arm64" if machine == "aarch64" else machine
        is_exact_arch = mac_machine in name
        if not is_exact_arch and "universal2" not in name:
            return -1
        floor_match = re.search(r"macosx_(\d+)_(\d+)", name)
        floor = (
            (int(floor_match.group(1)), int(floor_match.group(2)))
            if floor_match
            else None
        )
        host = _version_tuple(os_version)
        if floor and host and floor > host:
            return -1
        base = 2000 if is_exact_arch else 1000
        if floor and host:
            return base + floor[0] * 100 + floor[1]
        return base - (floor[0] * 100 + floor[1]) if floor else base

    if system == "windows":
        if "-win_" not in name:
            return -1
        win_machine = {
            "x86_64": "amd64",
            "aarch64": "arm64",
            "x86": "32",
        }.get(machine, machine)
        return 20 if f"win_{win_machine}" in name else -1

    return -1


def select_wheel_asset(
    release: dict[str, Any],
    *,
    system: str | None = None,
    machine: str | None = None,
    libc: str | None = None,
    libc_version: str | None = None,
    os_version: str | None = None,
) -> tuple[str, str]:
    system = system or platform.system()
    machine = machine or platform.machine()
    detected_libc, detected_libc_version = (
        _detect_linux_libc() if system.lower() == "linux" else ("", "")
    )
    if libc is None:
        libc = detected_libc
    libc = libc.lower()
    if libc_version is None:
        libc_version = detected_libc_version if libc == detected_libc else ""
    if os_version is None:
        os_version = platform.mac_ver()[0] if system.lower() == "darwin" else ""
    candidates: list[tuple[int, str, str]] = []

    for asset in release.get("assets", []):
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        score = _wheel_platform_score(
            name,
            system,
            machine,
            libc,
            libc_version,
            os_version,
        )
        if score >= 0:
            candidates.append((score, name, url))

    if not candidates:
        available = sorted(
            asset["name"]
            for asset in release.get("assets", [])
            if isinstance(asset, dict)
            and isinstance(asset.get("name"), str)
            and asset["name"].endswith(".whl")
        )
        suffix = f" Available wheel assets: {', '.join(available)}" if available else ""
        raise InstallError(
            f"latest release has no CPython 3.12+ abi3 wheel for "
            f"{system}/{machine}.{suffix}"
        )

    candidates.sort(reverse=True)
    _, name, url = candidates[0]
    if not url.startswith(RELEASE_DOWNLOAD_PREFIX):
        raise InstallError(f"refusing unexpected release asset URL: {url}")
    return name, url


def fetch_latest_release() -> dict[str, Any]:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "file-tools-plugin-installer",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise InstallError(
                "krkawzq/file-tools has no published GitHub release yet"
            ) from exc
        raise InstallError(
            f"GitHub latest-release request failed with HTTP {exc.code}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise InstallError(f"failed to read the latest GitHub release: {exc}") from exc

    if not isinstance(payload, dict):
        raise InstallError("GitHub latest-release response was not a JSON object")
    return payload


def download_asset(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "file-tools-plugin-installer"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            with destination.open("wb") as output:
                shutil.copyfileobj(response, output)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InstallError(f"failed to download release wheel: {exc}") from exc


def _run(command: Sequence[str]) -> None:
    try:
        subprocess.run(list(command), check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InstallError(f"command failed: {' '.join(command)}") from exc


def _python_version(executable: Path) -> tuple[int, int]:
    try:
        result = subprocess.run(
            [
                str(executable),
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        major, minor = result.stdout.strip().split(".", 1)
        return int(major), int(minor)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise InstallError(f"cannot inspect Python at {executable}") from exc


def _fallback_python() -> Path:
    candidates = [
        sys.executable,
        "python3.14",
        "python3.13",
        "python3.12",
        "python3",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        resolved = shutil.which(candidate)
        executable = Path(resolved or candidate)
        key = str(executable)
        if key in seen or not executable.exists():
            continue
        seen.add(key)
        try:
            if _python_version(executable) >= (3, 12):
                return executable
        except InstallError:
            continue
    raise InstallError(
        "uv is unavailable and no Python 3.12+ interpreter was found"
    )


def ensure_venv(plugin_root: Path) -> tuple[Path, str]:
    venv = plugin_root / ".venv"
    python = _venv_python(venv)
    uv = shutil.which("uv")

    if not python.exists():
        if uv:
            _run([uv, "venv", "--python", "3.12", str(venv)])
            method = "uv"
        else:
            _run([str(_fallback_python()), "-m", "venv", str(venv)])
            method = "venv"
    else:
        method = "uv" if uv else "venv"

    if _python_version(python) < (3, 12):
        raise InstallError(
            f"{python} is older than Python 3.12; replace this plugin-local .venv"
        )
    ensure_manifest_python_path(venv, python)
    return python, method


def ensure_manifest_python_path(
    venv: Path,
    python: Path,
    *,
    windows: bool | None = None,
) -> None:
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return

    manifest_python = venv / "bin" / "python.exe"
    manifest_python.parent.mkdir(parents=True, exist_ok=True)
    if manifest_python.exists():
        manifest_python.unlink()
    try:
        os.link(python, manifest_python)
    except OSError:
        shutil.copy2(python, manifest_python)


def install_runtime(python: Path, method: str, wheel: Path) -> None:
    if method == "uv":
        uv = shutil.which("uv")
        if not uv:
            raise InstallError("uv disappeared while installing the runtime")
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--upgrade",
                str(wheel),
                FASTMCP_REQUIREMENT,
                SOCKSIO_REQUIREMENT,
            ]
        )
    else:
        _run([str(python), "-m", "ensurepip", "--upgrade"])
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                str(wheel),
                FASTMCP_REQUIREMENT,
                SOCKSIO_REQUIREMENT,
            ]
        )


def verify_runtime(python: Path) -> None:
    _run(
        [
            str(python),
            "-c",
            (
                "import file_tools, fastmcp, socksio; "
                "from file_tools._core import count_lines; "
                "from file_tools.cli.mcp_server import create_mcp_server; "
                "create_mcp_server(); "
                "print('File Tools plugin runtime OK:', file_tools.__file__)"
            ),
        ]
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install the latest krkawzq/file-tools release wheel, fastmcp, "
            "and socksio into <plugin-root>/.venv."
        )
    )
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=_plugin_root_from_script(),
        help="actual installed plugin root (defaults to the root containing this skill)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    plugin_root = args.plugin_root.expanduser().resolve()
    if not (plugin_root / "skills" / "config-file-tools" / "SKILL.md").is_file():
        raise InstallError(
            f"{plugin_root} is not a File Tools plugin root: skill is missing"
        )

    release = fetch_latest_release()
    wheel_name, wheel_url = select_wheel_asset(release)
    tag = release.get("tag_name", "<unknown>")
    print(f"Selected File Tools release {tag}: {wheel_name}")

    python, method = ensure_venv(plugin_root)
    with tempfile.TemporaryDirectory(prefix="file-tools-release-") as temp_dir:
        wheel = Path(temp_dir) / wheel_name
        download_asset(wheel_url, wheel)
        install_runtime(python, method, wheel)
    verify_runtime(python)
    print(f"Installed plugin runtime with {method}: {python}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
