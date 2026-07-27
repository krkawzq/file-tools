import inspect
from collections.abc import Awaitable
from pathlib import Path
from typing import Callable

import pytest

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
