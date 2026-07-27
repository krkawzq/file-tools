---
name: config-file-tools
description: >
  Build and install the file-tools project from source.
  Compiles the Rust extension (pyo3 abi3) via cargo/maturin,
  installs the Python package, and verifies the MCP server.
  Use when file-tools needs to be rebuilt after Rust changes,
  on a fresh clone, or when the MCP server fails to start.
---

# Config File Tools

Build, install, and verify the file-tools project.

## Overview

file-tools has two components that must be built together:

1. **Rust extension** (`src/core-rs/`) — pyo3 native module providing edit matching, line slicing, and patch application. Compiled to `src/file_tools/_core.abi3.so` (CPython 3.12+ stable ABI).
2. **Python package** (`src/file_tools/`) — tools, clients, MCP server, and CLI. Imported via `PYTHONPATH=./src`.

## Quick Start (full build + install)

```bash
# 1. Install build prerequisites
pip install fastmcp maturin

# 2. Build Rust extension + install in dev mode (one command)
maturin develop --release

# 3. Verify
python -c "from file_tools._core import count_lines; print('Rust core OK')"
python -m file_tools.cli.mcp_server --help
```

`maturin develop --release` does three things:
- Runs `cargo build --release --features pyo3/extension-module`
- Copies `target/release/lib_core.so` → `src/file_tools/_core.abi3.so`
- Installs the Python package in editable mode

## Step-by-Step (manual control)

### Build Rust extension only

```bash
cargo build --release --features extension-module
```

Output: `target/release/lib_core.so`. Copy it into place:

```bash
cp target/release/lib_core.so src/file_tools/_core.abi3.so
```

### Install Python package only

With the `.so` in place, install the package in editable mode:

```bash
pip install -e .
```

This registers the console entry points (`file-tools`, `file-tools-mcp`) and
installs the `fastmcp` dependency. The `-e` flag means source changes take
effect immediately without re-installing.

### Rust-only rebuild (no Python changes)

```bash
cargo build --release --features extension-module
cp target/release/lib_core.so src/file_tools/_core.abi3.so
```

No need to re-run `pip install -e .` unless Python dependencies changed.

## Rebuild After `cargo clean`

`cargo clean` removes `target/`. A full rebuild:

```bash
maturin develop --release
```

Or step-by-step:

```bash
cargo build --release --features extension-module
cp target/release/lib_core.so src/file_tools/_core.abi3.so
```

## Verification Checklist

After building, verify each layer:

```bash
# 1. Rust extension loads
python -c "from file_tools._core import count_lines, edit_text; print('OK')"

# 2. Python tools import
python -c "from file_tools import read, write, edit, apply_patch, bash; print('OK')"

# 3. MCP server starts (stdio transport — will block waiting for input; Ctrl+C to stop)
timeout 2 python -m file_tools.cli.mcp_server 2>&1 || true

# 4. CLI works
python -m file_tools.cli.main read README.md --cwd "$(pwd)" --limit 5
```

## Troubleshooting

### `ModuleNotFoundError: No module named 'file_tools._core'`

The Rust extension is not built or not in the right place:

```bash
ls -la src/file_tools/_core.abi3.so   # should exist
file src/file_tools/_core.abi3.so     # should be an ELF shared object
```

Rebuild with `maturin develop --release`.

### `ImportError: ... undefined symbol`

The `.so` was built against a different Python version or ABI. The project
targets abi3-py312, so CPython 3.12+ is required. Check:

```bash
python --version  # must be 3.12+
```

If Python < 3.12, rebuild with the correct Python:

```bash
maturin develop --release -i python3.12
```

### `fastmcp` not found

```bash
pip install fastmcp
```

### MCP server starts but tools fail

Check `cwd` resolves correctly. The `.mcp.json` sets `cwd: "."` (project
root) and `PYTHONPATH: "./src"`. From the project root:

```bash
PYTHONPATH=./src python -c "from file_tools import read; print(read('README.md', offset=1, limit=3))"
```

## Build Environment

| Requirement | Version | Notes |
|---|---|---|
| Rust | stable (1.75+) | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| Python | 3.12+ | CPython; abi3 stable ABI |
| maturin | ≥1.8 | `pip install maturin` |
| fastmcp | ≥3.4 | `pip install fastmcp` |
| paramiko | ≥3.4 | Only needed for SSH client; `pip install paramiko` |

On this project's development machine (PJLab Brain H cluster), Rust and
Python 3.12+ are pre-installed. Only `fastmcp` and `maturin` need to be
added via pip.
