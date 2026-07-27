import asyncio
import json
import re
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_plugin_manifest_exposes_only_mcp_and_skill_surfaces() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / ".codex-plugin" / "plugin.json").read_text()
    )
    mcp_manifest = json.loads((PROJECT_ROOT / ".mcp.json").read_text())
    marketplace = json.loads(
        (PROJECT_ROOT / ".agents" / "plugins" / "marketplace.json").read_text()
    )

    assert manifest["name"] == "file-tools"
    assert manifest["repository"] == "https://github.com/krkawzq/file-tools"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "apps" not in manifest
    assert "hooks" not in manifest

    server = mcp_manifest["mcpServers"]["file-tools"]
    assert server["command"] == "python"
    assert server["args"] == ["-m", "file_tools.cli.mcp_server"]
    assert server["cwd"] == "."
    assert server["env"]["PYTHONPATH"] == "./src"

    assert marketplace["name"] == "file-tools"
    assert marketplace["interface"]["displayName"] == "File Tools"
    assert marketplace["plugins"] == [
        {
            "name": "file-tools",
            "source": {
                "source": "url",
                "url": "https://github.com/krkawzq/file-tools.git",
                "ref": "master",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Developer Tools",
        }
    ]

    prompt_text = json.dumps(manifest["interface"]["defaultPrompt"]).lower()
    skill_text = (
        PROJECT_ROOT / "skills" / "file-tools" / "SKILL.md"
    ).read_text().lower()
    assert re.search(r"\bcli\b", prompt_text) is None
    assert re.search(r"\bcli\b", skill_text) is None


def test_mcp_stdio_server_lists_file_tools() -> None:
    async def check_tools() -> None:
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "file_tools.cli.mcp_server"],
            cwd=str(PROJECT_ROOT),
            env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
        )
        async with Client(transport, timeout=30, init_timeout=30) as client:
            tools = await client.list_tools()

        assert {tool.name for tool in tools} == {
            "apply_patch",
            "bash",
            "edit",
            "read",
            "write",
        }

    asyncio.run(check_tools())
