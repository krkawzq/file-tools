import importlib.util
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER_PATH = (
    ROOT
    / "skills"
    / "config-file-tools"
    / "scripts"
    / "install_latest_release.py"
)
SPEC = importlib.util.spec_from_file_location(
    "file_tools_install_latest_release",
    INSTALLER_PATH,
)
assert SPEC is not None and SPEC.loader is not None
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


def _asset(name: str) -> dict[str, str]:
    return {
        "name": name,
        "browser_download_url": (
            "https://github.com/krkawzq/file-tools/releases/download/v0.1.0/"
            f"{name}"
        ),
    }


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        (
            "Linux",
            "x86_64",
            "mcp_file_tools-0.1.0-cp312-abi3-manylinux_2_17_x86_64.whl",
        ),
        (
            "Darwin",
            "arm64",
            "mcp_file_tools-0.1.0-cp312-abi3-macosx_11_0_arm64.whl",
        ),
        (
            "Windows",
            "AMD64",
            "mcp_file_tools-0.1.0-cp312-abi3-win_amd64.whl",
        ),
    ],
)
def test_select_wheel_asset_matches_platform(
    system: str,
    machine: str,
    expected: str,
) -> None:
    release = {
        "assets": [
            _asset("mcp_file_tools-0.1.0-cp312-abi3-win_amd64.whl"),
            _asset("mcp_file_tools-0.1.0-cp312-abi3-macosx_11_0_universal2.whl"),
            _asset("mcp_file_tools-0.1.0-cp312-abi3-macosx_11_0_arm64.whl"),
            _asset("mcp_file_tools-0.1.0-cp312-abi3-manylinux_2_17_x86_64.whl"),
        ]
    }

    name, _ = INSTALLER.select_wheel_asset(
        release,
        system=system,
        machine=machine,
        libc="glibc",
    )

    assert name == expected


def test_select_wheel_asset_reports_available_incompatible_wheels() -> None:
    release = {
        "assets": [
            _asset("mcp_file_tools-0.1.0-cp312-abi3-win_amd64.whl"),
        ]
    }

    with pytest.raises(INSTALLER.InstallError, match="win_amd64"):
        INSTALLER.select_wheel_asset(
            release,
            system="Linux",
            machine="x86_64",
            libc="glibc",
        )


def test_select_wheel_asset_distinguishes_glibc_and_musl() -> None:
    manylinux = "mcp_file_tools-0.1.0-cp312-abi3-manylinux_2_17_x86_64.whl"
    musllinux = "mcp_file_tools-0.1.0-cp312-abi3-musllinux_1_2_x86_64.whl"
    release = {"assets": [_asset(manylinux), _asset(musllinux)]}

    glibc_name, _ = INSTALLER.select_wheel_asset(
        release,
        system="Linux",
        machine="x86_64",
        libc="glibc",
        libc_version="2.17",
    )
    musl_name, _ = INSTALLER.select_wheel_asset(
        release,
        system="Linux",
        machine="x86_64",
        libc="musl",
        libc_version="1.2",
    )

    assert glibc_name == manylinux
    assert musl_name == musllinux


def test_select_wheel_asset_uses_automatic_musl_detection(monkeypatch) -> None:
    manylinux = "mcp_file_tools-0.1.0-cp312-abi3-manylinux_2_17_x86_64.whl"
    musllinux = "mcp_file_tools-0.1.0-cp312-abi3-musllinux_1_2_x86_64.whl"
    release = {"assets": [_asset(manylinux), _asset(musllinux)]}
    monkeypatch.setattr(
        INSTALLER,
        "_detect_linux_libc",
        lambda: ("musl", "1.2"),
    )

    selected, _ = INSTALLER.select_wheel_asset(
        release,
        system="Linux",
        machine="x86_64",
    )

    assert selected == musllinux


def test_detect_linux_libc_uses_ldd_for_musl(monkeypatch) -> None:
    monkeypatch.setattr(INSTALLER.platform, "libc_ver", lambda: ("", ""))
    monkeypatch.setattr(INSTALLER.sysconfig, "get_config_var", lambda key: "")
    monkeypatch.setattr(
        INSTALLER.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="musl libc (x86_64)\nVersion 1.2.4\n",
            stderr="",
        ),
    )

    assert INSTALLER._detect_linux_libc() == ("musl", "1.2")


def test_select_wheel_asset_rejects_too_new_glibc_floor() -> None:
    manylinux_2_17 = "mcp_file_tools-0.1.0-cp312-abi3-manylinux_2_17_x86_64.whl"
    manylinux_2_28 = "mcp_file_tools-0.1.0-cp312-abi3-manylinux_2_28_x86_64.whl"
    release = {"assets": [_asset(manylinux_2_17), _asset(manylinux_2_28)]}

    selected, _ = INSTALLER.select_wheel_asset(
        release,
        system="Linux",
        machine="x86_64",
        libc="glibc",
        libc_version="2.17",
    )

    assert selected == manylinux_2_17


def test_select_wheel_asset_rejects_unknown_linux_libc() -> None:
    release = {
        "assets": [
            _asset("mcp_file_tools-0.1.0-cp312-abi3-manylinux_2_17_x86_64.whl"),
            _asset("mcp_file_tools-0.1.0-cp312-abi3-musllinux_1_2_x86_64.whl"),
        ]
    }

    with pytest.raises(INSTALLER.InstallError, match="no CPython"):
        INSTALLER.select_wheel_asset(
            release,
            system="Linux",
            machine="x86_64",
            libc="",
            libc_version="",
        )


def test_select_wheel_asset_respects_macos_deployment_floor() -> None:
    macos_11 = "mcp_file_tools-0.1.0-cp312-abi3-macosx_11_0_arm64.whl"
    macos_14 = "mcp_file_tools-0.1.0-cp312-abi3-macosx_14_0_arm64.whl"
    release = {"assets": [_asset(macos_11), _asset(macos_14)]}

    selected, _ = INSTALLER.select_wheel_asset(
        release,
        system="Darwin",
        machine="arm64",
        os_version="13.6",
    )

    assert selected == macos_11


def test_fetch_latest_release_reports_missing_release(monkeypatch) -> None:
    def missing_release(*args, **kwargs):
        raise urllib.error.HTTPError(
            INSTALLER.LATEST_RELEASE_API,
            404,
            "Not Found",
            {},
            None,
        )

    monkeypatch.setattr(INSTALLER.urllib.request, "urlopen", missing_release)

    with pytest.raises(INSTALLER.InstallError, match="no published GitHub release"):
        INSTALLER.fetch_latest_release()


def test_pip_fallback_installs_wheel_and_fastmcp(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        INSTALLER,
        "_run",
        lambda command: commands.append(list(command)),
    )

    wheel = Path("/tmp/file_tools.whl")
    INSTALLER.install_runtime(
        Path("/plugin/.venv/bin/python"),
        "venv",
        wheel,
    )

    assert commands[0][-3:] == ["-m", "ensurepip", "--upgrade"]
    assert commands[1][-2:] == [
        str(wheel),
        INSTALLER.FASTMCP_REQUIREMENT,
    ]


def test_windows_manifest_python_alias_is_created(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    python = venv / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"venv-python")

    INSTALLER.ensure_manifest_python_path(venv, python, windows=True)

    assert (venv / "bin" / "python.exe").read_bytes() == b"venv-python"


def test_plugin_manifests_use_plugin_local_venv() -> None:
    configs = {
        "claude": json.loads(
            (ROOT / ".claude-plugin" / "plugin.json").read_text()
        )["mcpServers"]["file-tools"],
        "codex": json.loads((ROOT / ".mcp.json").read_text())["mcpServers"][
            "file-tools"
        ],
        "cursor": json.loads(
            (ROOT / ".cursor-plugin" / "plugin.json").read_text()
        )["mcpServers"]["file-tools"],
        "grok": json.loads(
            (ROOT / ".grok-plugin" / "plugin.json").read_text()
        )["mcpServers"]["file-tools"],
    }

    assert configs["claude"]["command"] == (
        "${CLAUDE_PLUGIN_ROOT}/.venv/bin/python"
    )
    assert configs["codex"]["command"] == "./.venv/bin/python"
    assert configs["codex"]["cwd"] == "."
    assert configs["cursor"]["command"] == (
        "${CURSOR_PLUGIN_ROOT}/.venv/bin/python"
    )
    assert configs["grok"]["command"] == (
        "${GROK_PLUGIN_ROOT}/.venv/bin/python"
    )
    assert all(
        config["args"] == ["-m", "file_tools.cli.mcp_server"]
        for config in configs.values()
    )
    assert all("PYTHONPATH" not in config.get("env", {}) for config in configs.values())
