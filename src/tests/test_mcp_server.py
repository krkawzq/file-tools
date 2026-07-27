import sys
from types import SimpleNamespace

from file_tools.cli import mcp_server


def test_create_mcp_server_registers_tools(monkeypatch) -> None:
    registered = []

    class FakeFastMCP:
        def __init__(self, name: str) -> None:
            self.name = name

    monkeypatch.setitem(
        sys.modules,
        "fastmcp",
        SimpleNamespace(FastMCP=FakeFastMCP),
    )
    monkeypatch.setattr(mcp_server, "register_tools", registered.append)

    server = mcp_server.create_mcp_server("custom-name")

    assert server.name == "custom-name"
    assert registered == [server]


def test_main_runs_mcp_server(monkeypatch) -> None:
    class FakeServer:
        def __init__(self) -> None:
            self.was_run = False

        def run(self) -> None:
            self.was_run = True

    server = FakeServer()
    monkeypatch.setattr(mcp_server, "create_mcp_server", lambda: server)

    mcp_server.main()

    assert server.was_run
