---
name: config-file-tools
description: Build, configure, diagnose, and verify the File Tools source checkout and MCP server. Trigger on a fresh clone, after Rust, Python, dependency, or launcher changes, when adding File Tools to an agent host, or when the MCP server fails to start, import its native extension, or expose all five tools.
---

# Configure File Tools

Set up File Tools from source and verify each layer from the Rust extension through MCP tool discovery. Inspect the target environment before changing dependencies or host configuration.

## Establish the target

1. Locate the source root containing `pyproject.toml`, `Cargo.toml`, `src/file_tools/`, `.mcp.json`, and the host plugin manifests (`.codex-plugin/`, `.claude-plugin/`, `.cursor-plugin/`).
2. Read those declarations and the relevant host configuration before acting; commands or entry points may have changed since this skill was written.
3. Confirm whether the goal is source development, rebuilding after code changes, or repairing an agent host's MCP launch configuration.
4. Use `uv` for Python environments and dependencies. Follow the environment's proxy and package-install rules before any networked `uv` or Cargo operation.
5. Do not assume a native extension built on another operating system, architecture, or Python floor is reusable.

## Build from source

From the source root, use the repository's declared development workflow:

```bash
uv sync --extra dev
uv run maturin develop --release --uv
```

This resolves the declared Python dependencies, builds the pyo3 extension with the configured CPython 3.12+ stable ABI, places it where the source package can import it, and installs the package into the uv-managed environment. Prefer this workflow over manually copying a platform-specific library. Re-run `uv sync --extra dev` when dependency declarations or the lockfile change; rebuild with Maturin after Rust code or build metadata changes.

## Configure the MCP launcher

Treat `.mcp.json` and `scripts/file_tools_mcp.py` as the repository's shared
launcher contract. Host-specific plugin manifests may wrap the same script
with `${CLAUDE_PLUGIN_ROOT}` / `${GROK_PLUGIN_ROOT}` absolute-style paths.

The checked-in launcher expects:

- `python ./scripts/file_tools_mcp.py` (or the same script under a plugin-root
  absolute path) over stdio;
- the script to locate the source/plugin root from its own file path and add
  `<root>/src` to `sys.path` / `PYTHONPATH` (process CWD must not be required);
- the selected Python interpreter to provide `fastmcp` and load `file_tools._core`.

Prefer the portable script over hand-written `cwd` / `PYTHONPATH=./src` entries.
Use an absolute interpreter only for an explicitly machine-local configuration.
Do not write credentials into MCP config. After changing host or plugin
configuration, use that host's supported refresh or reinstall flow and start a
new agent thread if it caches tools or skills.

## Verify in layers

Run the smallest checks that prove the repaired layer, then expand verification after code or build changes:

```bash
# Native extension and Python API
uv run python -c "from file_tools._core import count_lines; from file_tools import read, write, edit, apply_patch, bash; print('imports OK')"

# CLI and MCP server construction
uv run file-tools --help
uv run python -c "from file_tools.cli.mcp_server import create_mcp_server; create_mcp_server(); print('server construction OK')"

# MCP stdio discovery via portable launcher
uv run python scripts/file_tools_mcp.py &
# or exercise the host's MCP list-tools against the same entry point
```

For a substantive source change, run the broader project checks as appropriate:

```bash
uv run pytest
cargo test --no-default-features
```

Verify that MCP discovery returns exactly `read`, `write`, `edit`, `apply_patch`, and `bash`. A server process merely starting is not enough.

## Diagnose by layer

- If `file_tools._core` is missing or incompatible, confirm Python is CPython 3.12+, confirm platform and architecture, and rebuild with `uv run maturin develop --release --uv`.
- If `fastmcp` is missing, run `uv sync --extra dev` and verify that the MCP launcher uses the same environment; do not install into an unrelated interpreter.
- If the CLI works but the agent host cannot start the server, compare the host's effective `command`, `args`, `cwd`, environment, and executable lookup with `.mcp.json`.
- If the server starts but tools are absent, run the focused stdio discovery test and inspect server-side registration rather than only testing imports.
- If SSH operations fail after the server is healthy, treat authentication, host-key verification, port, and remote `cwd` as runtime client configuration, not as build failures.

## Preserve configuration integrity

- Keep `pyproject.toml` and `uv.lock` authoritative for Python dependencies; do not hand-edit `.venv`.
- Keep Cargo features aligned with `[tool.maturin]`; do not invent alternate library names or copy paths without checking build output.
- Do not weaken SSH host-key verification or embed passwords to make a smoke test pass.
- Report which layer was verified and the exact remaining failure when setup cannot be completed.
