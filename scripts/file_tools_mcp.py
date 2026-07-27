#!/usr/bin/env python3
"""Portable MCP entry for project-local and plugin installs.

Resolves the repository/plugin root from this file's location so import and
cwd do not depend on the process working directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    src = root / "src"
    sys.path.insert(0, str(src))
    os.environ["PYTHONPATH"] = (
        f"{src}{os.pathsep}{os.environ['PYTHONPATH']}"
        if os.environ.get("PYTHONPATH")
        else str(src)
    )
    os.chdir(root)

    from file_tools.cli.mcp_server import main as mcp_main

    mcp_main()


if __name__ == "__main__":
    main()
