---
name: config-file-tools
description: Build, configure, diagnose, and verify the File Tools source checkout and MCP server. Trigger on a fresh clone, after Rust/Python/dependency/launcher changes, when adding File Tools to an agent host, or when the MCP server fails to start, import its native extension, or expose all five tools.
---

# Configure File Tools

Bring up File Tools from source and verify each layer: native extension → Python API → CLI → MCP discovery. Inspect the environment before changing dependencies or host config.

## Establish the target

1. Find the source root: `pyproject.toml`, `Cargo.toml`, `src/file_tools/`, `scripts/file_tools_mcp.py`, `.mcp.json`, and host manifests (`.codex-plugin/`, `.claude-plugin/`, `.grok-plugin/`, `.cursor-plugin/`).
2. Read those declarations and the host's MCP / plugin config before changing anything.
3. Clarify goal: source dev, rebuild after code change, or fix an agent host launcher.
4. Use `uv` for Python env and deps. Apply the environment's proxy / package-install rules before networked `uv` or Cargo.
5. Native extensions are not portable across OS, arch, or Python floors — rebuild when those change.

## Build from source

```bash
uv sync --extra dev
uv run maturin develop --release --uv
```

Resolves Python deps, builds `file_tools._core` (pyo3, CPython **3.12+ abi3**), and installs into the uv env. Re-`uv sync --extra dev` when lock/deps change; rebuild Maturin after Rust or build-metadata changes.

## MCP launcher

Canonical entry: `scripts/file_tools_mcp.py` (also `.mcp.json`).

| Expectation | Detail |
|---|---|
| Command | `python scripts/file_tools_mcp.py` over stdio (or the same script under a host plugin-root absolute path) |
| Host wrappers | e.g. `${CLAUDE_PLUGIN_ROOT}` / `${GROK_PLUGIN_ROOT}` + `/scripts/file_tools_mcp.py` |
| Import path | Launcher resolves repo/plugin root from its own path and puts `<root>/src` on `sys.path` / `PYTHONPATH` |
| Runtime | Interpreter must provide `fastmcp` and load `file_tools._core` |

Use an absolute interpreter only for a machine-local pin. Never put credentials in MCP config. After host/plugin config changes, use that host's refresh/reinstall flow; start a new agent thread if tools/skills are cached.

## Verify in layers

Smallest proof first, then expand:

```bash
# 1) Native + public Python API
uv run python -c "from file_tools._core import count_lines; from file_tools import read, write, edit, apply_patch, bash; print('imports OK')"

# 2) CLI + server construction
uv run file-tools --help
uv run python -c "from file_tools.cli.mcp_server import create_mcp_server; create_mcp_server(); print('server construction OK')"

# 3) MCP stdio entry (or host list-tools against the same entry)
uv run python scripts/file_tools_mcp.py
```

After substantive source changes:

```bash
uv run pytest
cargo test --no-default-features
```

**MCP discovery must list exactly:** `read`, `write`, `edit`, `apply_patch`, `bash`. Process start alone is insufficient.

Optional docstring smoke (agent-facing copy lives on registered tools):

```bash
uv run python -c "
from file_tools.mcp.register import register_tools
class M:
    def __init__(self): self.tools = {}
    def tool(self, f): self.tools[f.__name__] = f; return f
m = M(); register_tools(m)
assert set(m.tools) == {'read','write','edit','apply_patch','bash'}
assert all(m.tools[n].__doc__ for n in m.tools)
print('tool docs OK')
"
```

## Diagnose by layer

| Symptom | Check |
|---|---|
| `file_tools._core` missing / bad ABI | CPython 3.12+, OS/arch; `uv run maturin develop --release --uv` |
| `fastmcp` missing | `uv sync --extra dev`; same env as the launcher |
| CLI OK, host won't start server | Host `command` / `args` / env vs `.mcp.json` or plugin manifest |
| Server up, tools missing | Stdio list-tools + registration path, not imports alone |
| Tools OK, SSH fails | Auth, host keys, port, remote `cwd` — runtime client config, not build |

## Integrity

- `pyproject.toml` + `uv.lock` own Python deps; do not hand-edit `.venv`.
- Keep Cargo features aligned with `[tool.maturin]`.
- Do not weaken host-key checks or embed passwords for smoke tests.
- Report verified layer and remaining failure when setup cannot finish.
