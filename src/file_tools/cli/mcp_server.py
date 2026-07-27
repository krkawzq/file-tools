"""Console entry point for the file-tools MCP server."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..mcp.mcp import register_tools

if TYPE_CHECKING:
    from fastmcp import FastMCP


def create_mcp_server(name: str = "file-tools") -> FastMCP:
    """Create a FastMCP server and register all file tools."""
    try:
        from fastmcp import FastMCP
    except ImportError as e:
        raise ImportError(
            "MCP support requires fastmcp. Install with: "
            "uv add --optional mcp fastmcp  or  pip install 'file-tools[mcp]'"
        ) from e

    mcp = FastMCP(name)
    register_tools(mcp)
    return mcp


def main() -> None:
    """Create and run the MCP server using FastMCP's default transport."""
    create_mcp_server().run()


if __name__ == "__main__":
    main()
