import inspect
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Callable

import pytest

from file_tools.mcp import tools as mcp_tools
from file_tools.mcp.register import register_tools


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Awaitable[str]]] = {}

    def tool(
        self,
        function: Callable[..., Awaitable[str]],
    ) -> Callable[..., Awaitable[str]]:
        self.tools[function.__name__] = function
        return function


def test_registered_mcp_tools_are_async() -> None:
    mcp = _FakeMcp()
    register_tools(mcp)

    assert set(mcp.tools) == {"read", "write", "edit", "apply_patch", "bash"}
    assert all(inspect.iscoroutinefunction(tool) for tool in mcp.tools.values())


def test_registered_docs_explain_fixed_limits_and_text_normalization() -> None:
    mcp = _FakeMcp()
    register_tools(mcp)

    for name in ("read", "write", "edit", "apply_patch"):
        doc = mcp.tools[name].__doc__ or ""
        assert "16 MiB" in doc
        assert "30-second" in doc
    assert "symbolic link" in (mcp.tools["read"].__doc__ or "")
    assert "CRLF" in (mcp.tools["edit"].__doc__ or "")
    assert "Invalid UTF-8 bytes" in (mcp.tools["apply_patch"].__doc__ or "")


def test_local_mcp_client_drops_ssh_only_cache_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_get_cached_client(**settings: Any) -> object:
        captured.update(settings)
        return sentinel

    monkeypatch.setattr(mcp_tools, "_get_cached_client", fake_get_cached_client)

    result = mcp_tools._client(
        cwd="/workspace",
        client=" LOCAL ",
        ssh_host="ignored-host",
        ssh_port=2222,
        ssh_user="ignored-user",
        ssh_password="ignored-secret",
        ssh_key="ignored-key",
        ssh_flags="-A",
        ssh_accept_unknown_host_key=True,
    )

    assert result is sentinel
    assert captured == {"client": "local", "cwd": "/workspace"}


@pytest.mark.anyio
async def test_registered_mcp_tools_execute_against_local_client(
    tmp_path: Path,
) -> None:
    mcp = _FakeMcp()
    register_tools(mcp)

    assert {"read", "write", "edit", "apply_patch", "bash"} <= set(mcp.tools)

    cwd = str(tmp_path)
    write_result = await mcp.tools["write"](
        "example.txt", "hello\n", cwd, client="local"
    )
    assert "wrote 6 bytes" in write_result
    assert (
        await mcp.tools["read"](
            "example.txt",
            cwd,
            show_line_numbers=False,
            client="local",
        )
        == "hello\n"
    )

    edit_result = await mcp.tools["edit"](
        "example.txt", "hello", "world", cwd, client="local"
    )
    assert edit_result.startswith("replaced ")
    assert "1 matches" in edit_result

    create_result = await mcp.tools["edit"](
        "created.txt", "", "created", cwd, client="local"
    )
    assert create_result.endswith("created.txt")
    assert create_result.startswith("created ")

    prepend_result = await mcp.tools["edit"](
        "example.txt",
        "",
        "header\n",
        cwd,
        prepend=True,
        client="local",
    )
    assert prepend_result.endswith("example.txt")
    assert prepend_result.startswith("prepended to ")

    patch_result = await mcp.tools["apply_patch"](
        "*** Begin Patch\n"
        "*** Update File: example.txt\n"
        "@@\n"
        "-world\n"
        "+patched\n"
        "*** End Patch\n",
        cwd,
        client="local",
    )
    assert "modified=['example.txt']" in patch_result
    assert (tmp_path / "example.txt").read_text() == "header\npatched\n"

    bash_out = await mcp.tools["bash"]("echo bash-ok", cwd, client="local")
    assert "bash-ok" in bash_out
