import inspect
from pathlib import Path
from typing import Callable

from file_tools.mcp.server import register_tools
from file_tools.client import clear_client_cache


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., str]] = {}

    def tool(self, function: Callable[..., str]) -> Callable[..., str]:
        self.tools[function.__name__] = function
        return function


def test_apply_patch_doc_shows_exact_control_and_context_prefixes() -> None:
    mcp = _FakeMcp()
    register_tools(mcp)
    doc = inspect.cleandoc(mcp.tools["apply_patch"].__doc__ or "")

    assert "documentation" in doc
    assert "delimiters only" in doc
    assert (
        "```text\n"
        "*** Begin Patch\n"
        "*** Update File: settings.txt\n"
        "@@\n"
        " [theme]\n"
        "-color=blue\n"
        "+color=green\n"
        "*** End Patch\n"
        "```"
    ) in doc


def test_edit_doc_explains_create_prepend_and_append_modes() -> None:
    mcp = _FakeMcp()
    register_tools(mcp)
    doc = inspect.cleandoc(mcp.tools["edit"].__doc__ or "")

    assert "complete new-file content" in doc
    assert "``append`` is not provided by this tool" in doc
    assert "combined with a non-empty ``old_string``" in doc


def test_registered_mcp_tools_execute_against_local_client(tmp_path: Path) -> None:
    clear_client_cache()
    mcp = _FakeMcp()
    register_tools(mcp)

    assert {"read", "write", "edit", "apply_patch", "bash"} <= set(mcp.tools)

    cwd = str(tmp_path)
    write_result = mcp.tools["write"]("example.txt", "hello\n", cwd, client="local")
    assert "wrote 6 bytes" in write_result
    assert (
        mcp.tools["read"](
            "example.txt",
            cwd,
            show_line_numbers=False,
            client="local",
        )
        == "hello\n"
    )

    edit_result = mcp.tools["edit"](
        "example.txt", "hello", "world", cwd, client="local"
    )
    assert edit_result.startswith("replaced ")
    assert "1 matches" in edit_result

    create_result = mcp.tools["edit"](
        "created.txt", "", "created", cwd, client="local"
    )
    assert create_result.endswith("created.txt")
    assert create_result.startswith("created ")

    prepend_result = mcp.tools["edit"](
        "example.txt",
        "",
        "header\n",
        cwd,
        prepend=True,
        client="local",
    )
    assert prepend_result.endswith("example.txt")
    assert prepend_result.startswith("prepended to ")

    patch_result = mcp.tools["apply_patch"](
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

    bash_out = mcp.tools["bash"]("echo thin-wrapper", cwd, client="local")
    assert "thin-wrapper" in bash_out
