---
name: config-file-tools
description: Install, build, configure, diagnose, and verify File Tools from either a source checkout or a GitHub-installed plugin for Claude Code, Codex, Cursor, and Grok. Trigger on fresh setup, marketplace/plugin installation, host migration, plugin update, Rust/Python/dependency/launcher changes, or failures to start MCP, import file_tools._core, use fastmcp, or expose all five tools.
---

# Configure File Tools

Bring up File Tools through the path the selected host actually runs. Verify each layer: native extension → Python API → CLI/server construction → MCP discovery → host activation.

## Choose the runtime root

Distinguish these workflows before building:

- **Source development:** build in the checkout containing the code being edited.
- **GitHub/marketplace installation:** install first, resolve the host's actual installed plugin root, and build there.

The remote repository does not carry a portable generated `file_tools._core` binary. Building a nearby checkout does not repair a host that runs a copied or cached plugin directory.

Confirm the selected root with `realpath`. It must contain `pyproject.toml`, `Cargo.toml`, `src/file_tools/`, `scripts/file_tools_mcp.py`, and the selected host's manifest. Do not infer it from the current working directory or hard-code a cache version.

## Install and locate by host

Honor the machine's shell, proxy, and package-install policy. Use an interactive shell when required aliases or functions live in its startup files.

| Host | GitHub installation | Resolve the installed root |
|---|---|---|
| Codex | `codex plugin marketplace add krkawzq/file-tools`, then `codex plugin add file-tools@file-tools --json` | Capture `installedPath` from the JSON result. For an existing install, confirm `file-tools@file-tools` with `codex plugin list` and inspect the configured plugin/cache metadata. Do not select `file-tools@personal` when GitHub was requested. |
| Claude Code | `claude plugin marketplace add krkawzq/file-tools`, then `claude plugin install file-tools@file-tools` | Use `claude plugin list` / `claude plugin details file-tools@file-tools` and the host's plugin metadata to identify the enabled installed copy. Runtime paths use `${CLAUDE_PLUGIN_ROOT}`. |
| Grok | `grok plugin marketplace add krkawzq/file-tools`, then `grok plugin install file-tools --trust` | Use `grok plugin list` / `grok plugin details file-tools` and the host's plugin metadata to identify the enabled installed copy. Runtime paths use `${GROK_PLUGIN_ROOT}`. |
| Cursor | Install the GitHub repository through the available Cursor marketplace or team catalog | Inspect `.cursor-plugin/plugin.json` plus Cursor's installed-plugin details/config and resolve the copy Cursor actually launches. Do not substitute the development checkout unless Cursor is explicitly configured to use it. |

Add a marketplace only when it is not already configured. Refresh or update it with that host's native command when the user requests the latest revision. Prefer a machine-readable install result when available; otherwise use host details/config. Treat a filesystem search of host caches as a last resort and confirm the candidate's name, version, manifest, and enabled registration before building it.

## Build in the selected root

Run from the resolved source or installed plugin root:

```bash
cd "$FILE_TOOLS_ROOT"
uv sync --extra dev
uv run maturin develop --release --uv
```

This creates a root-local `.venv`, resolves Python dependencies, builds the pyo3 **CPython 3.12+ abi3** extension, and installs the package editable. Expect a generated file such as `src/file_tools/_core.abi3.so`.

Re-run `uv sync --extra dev` when lock/dependencies change. Re-run Maturin after Rust, Python binding, build metadata, OS, architecture, or Python-floor changes. Never copy a native extension from another checkout, host, OS, or architecture.

For a plugin update, resolve the installed root again: hosts commonly install a new version into a new cache directory, leaving the previous compiled directory intact but unused.

## Match the host launcher

Read the selected host manifest and its effective MCP config before changing it:

| Host surface | Expected entry |
|---|---|
| Codex | `.mcp.json` → `python ./scripts/file_tools_mcp.py` |
| Claude Code | `.claude-plugin/plugin.json` → `python ${CLAUDE_PLUGIN_ROOT}/scripts/file_tools_mcp.py` |
| Grok | `.grok-plugin/plugin.json` → `python ${GROK_PLUGIN_ROOT}/scripts/file_tools_mcp.py` |
| Cursor | `.cursor-plugin/plugin.json` → `python -m file_tools.cli.mcp_server` with plugin-root `cwd` and `PYTHONPATH=./src` |

The portable launcher resolves its root from its own file and prepends `<root>/src`. The effective interpreter must still provide `fastmcp` and load the native extension from that same root.

If `<root>/.venv/bin/python` succeeds but the host's exact `python` fails, fix the interpreter mismatch in a supported machine-local host config or launcher override. Prefer an absolute `<root>/.venv/bin/python` for a machine-local pin. Do not commit a machine-specific cache path to a distributable manifest, and re-resolve it after plugin updates.

## Verify the installed copy in layers

Set `FILE_TOOLS_ROOT` to the proven runtime root, not a convenient checkout:

```bash
cd "$FILE_TOOLS_ROOT"

# 1) Build artifact and plugin-local environment
find src/file_tools -maxdepth 1 -type f -name "_core*.so" -print
.venv/bin/python -c "from file_tools._core import count_lines; from file_tools import read, write, edit, apply_patch, bash; print('plugin venv imports OK')"

# 2) Exact default launcher interpreter
PYTHONPATH="$FILE_TOOLS_ROOT/src" python -c "import file_tools; import file_tools.cli.mcp_server; print('host interpreter imports OK', file_tools.__file__)"

# 3) CLI and server construction
.venv/bin/file-tools --help
.venv/bin/python -c "from file_tools.cli.mcp_server import create_mcp_server; create_mcp_server(); print('server construction OK')"
```

Then invoke the effective host MCP entry or perform a stdio list-tools request against it. **MCP discovery must list exactly:** `read`, `write`, `edit`, `apply_patch`, `bash`. Process start or imports alone are insufficient.

Finally, verify the selected host reports the intended remote plugin as installed and enabled. Restart or open a new agent thread after install, update, build, or config changes when that host caches skills or MCP processes.

For substantive source changes, additionally run:

```bash
uv run pytest
cargo test --no-default-features
```

## Diagnose by layer

| Symptom | Likely cause and check |
|---|---|
| Plugin is enabled but `file_tools._core` is missing | The GitHub-installed copy was never built, or a different root was built. Resolve the enabled root and build there. |
| A development checkout imports, but the host fails | The host runs another cached/copied root. Inspect the effective plugin registration and launcher path. |
| `.venv/bin/python` works, host `python` lacks `fastmcp` | The launcher uses a different interpreter. Apply a supported machine-local interpreter override. |
| Native import reports a bad ABI | Check CPython 3.12+, OS, and architecture; rebuild in the runtime root. |
| Update reintroduces the import failure | The host activated a new version directory. Resolve and build the new installed root. |
| CLI works, host cannot start MCP | Compare effective `command`, `args`, `cwd`, environment, and plugin-root expansion with the host manifest. |
| Server starts, tools are missing | Perform MCP list-tools and inspect registration; do not stop at process startup. |
| Tools work locally, SSH operations fail | Diagnose auth, host keys, port, and remote `cwd`; this is runtime client configuration, not a build issue. |

## Integrity

- Keep `pyproject.toml` and `uv.lock` authoritative; do not hand-edit `.venv`.
- Keep Cargo features aligned with `[tool.maturin]`.
- Preserve host-specific manifests and root variables; do not force one host's paths or commands onto the others.
- Do not weaken SSH host-key checks or embed credentials in smoke tests.
- Report the exact host, plugin identity, runtime root, verified layer, and remaining failure when setup cannot finish.
