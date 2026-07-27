import importlib.util
import json
import urllib.error
from pathlib import Path

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
            "file_tools-0.1.0-cp312-abi3-manylinux_2_17_x86_64.whl",
        ),
        (
            "Darwin",
            "arm64",
            "file_tools-0.1.0-cp312-abi3-macosx_11_0_arm64.whl",
        ),
        (
            "Windows",
            "AMD64",
            "file_tools-0.1.0-cp312-abi3-win_amd64.whl",
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
            _asset("file_tools-0.1.0-cp312-abi3-win_amd64.whl"),
            _asset("file_tools-0.1.0-cp312-abi3-macosx_11_0_universal2.whl"),
            _asset("file_tools-0.1.0-cp312-abi3-macosx_11_0_arm64.whl"),
            _asset("file_tools-0.1.0-cp312-abi3-manylinux_2_17_x86_64.whl"),
        ]
    }

    name, _ = INSTALLER.select_wheel_asset(
        release,
        system=system,
        machine=machine,
    )

    assert name == expected


def test_select_wheel_asset_reports_available_incompatible_wheels() -> None:
    release = {
        "assets": [
            _asset("file_tools-0.1.0-cp312-abi3-win_amd64.whl"),
        ]
    }

    with pytest.raises(INSTALLER.InstallError, match="win_amd64"):
        INSTALLER.select_wheel_asset(
            release,
            system="Linux",
            machine="x86_64",
        )


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

    INSTALLER.install_runtime(
        Path("/plugin/.venv/bin/python"),
        "venv",
        Path("/tmp/file_tools.whl"),
    )

    assert commands[0][-3:] == ["-m", "ensurepip", "--upgrade"]
    assert commands[1][-2:] == [
        "/tmp/file_tools.whl",
        INSTALLER.FASTMCP_REQUIREMENT,
    ]


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
