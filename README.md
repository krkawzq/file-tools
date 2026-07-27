# file-tools

<p align="center">
  <img src="assets/file-tools.svg" alt="file-tools" width="96" height="96" />
</p>

<p align="center">
  <strong>Precise file ops &amp; bounded commands for agents — local or SSH.</strong>
</p>

<p align="center">
  <a href="https://github.com/krkawzq/file-tools"><img alt="GitHub" src="https://img.shields.io/badge/github-krkawzq%2Ffile--tools-181717?logo=github" /></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white" /></a>
  <a href="https://www.rust-lang.org/"><img alt="Rust" src="https://img.shields.io/badge/core-Rust%20%2B%20PyO3-DEA584?logo=rust&logoColor=white" /></a>
  <a href="https://modelcontextprotocol.io/"><img alt="MCP" src="https://img.shields.io/badge/MCP-stdio-black?logo=linkerd&logoColor=white" /></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey" />
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green" />
</p>

---

## What it is

**file-tools** gives coding agents a small, explicit toolkit for working with files and foreground commands:

| Surface | Purpose |
|---|---|
| **read** | Line windows and tails (agent-friendly offsets) |
| **write** | Full-file create / replace |
| **edit** | Unique-match literal edits |
| **apply_patch** | Multi-file structured patches |
| **bash** | Bounded foreground commands |

Same tools, three ways in:

```text
Python API  ·  MCP server  ·  CLI
```

Work **locally** or over **SSH** with the same interface — pick a client, pass `cwd`, go.

> Prefer the host’s built-in Read / Write / Edit / Bash for ordinary local work.
> Use file-tools when you need **SSH-backed** work, **stricter edit/patch** semantics, or an explicit MCP surface.

---

## Why agents use it

- **One workspace at a time** — every call takes an explicit `cwd` (local path or remote path).
- **Local and remote parity** — `client="local"` or `client="ssh"` on every tool.
- **Safe-by-default SSH** — system OpenSSH, keys/agent/`~/.ssh/config`; explicit passwords stay out of process argv.
- **Bounded file I/O** — line windows stream with a configurable transfer cap instead of loading whole files into Python.
- **Conflict-aware writes** — atomic replace, version checks, and deterministic patch rollback prevent silent lost updates.
- **Predictable agent contract** — 1-based line offsets; negative offset means “tail”.
- **Bounded shell** — timeouts, retained head/tail output, no sandbox theatre, no hidden background manager.
- **Host-ready** — ships as MCP + plugins for **Codex**, **Claude Code**, **Grok**, and **Cursor**.

---

## Quick start

### Library

```bash
uv add file-tools   # or: pip install file-tools
# from source (native extension):
uv sync --extra dev && uv run maturin develop --release --uv
```

```python
from file_tools import read, edit, get_client

client = get_client(client="local", cwd="/path/to/project")
print(read("README.md", offset=1, limit=40, client=client).content)

edit("src/app.py", "old", "new", client=client)
```

### MCP

```bash
# from a source checkout (portable launcher)
python scripts/file_tools_mcp.py
# or, with the package installed:
file-tools-mcp
```

Tools exposed: `read` · `write` · `edit` · `apply_patch` · `bash`

### CLI

```bash
file-tools read README.md --cwd "$PWD" --limit 40
printf 'hello\n' | file-tools write notes.txt --cwd "$PWD"
file-tools bash 'git status --short' --cwd "$PWD"
```

---

## SSH in one glance

```python
from file_tools import get_client, read

client = get_client(
    client="ssh",
    ssh_host="host.example.com",
    ssh_port=22,
    ssh_user="user",
    cwd="/home/user/project",
)
print(read("README.md", client=client).content)
```

The Python API, CLI, and MCP tools accept the same `client` and `ssh_xxx`
parameters for all five tools. `ssh_host` may be an alias from
`~/.ssh/config`. Prefer keys/agent over passwords. Repeated MCP calls reuse
cached clients and OpenSSH control sockets internally.

---

## Agent plugins

Install from the remote repo **`krkawzq/file-tools`** (not a local path).

<table>
  <thead>
    <tr>
      <th align="left">Host</th>
      <th align="left">Install</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>Codex</strong></td>
      <td>

```bash
codex plugin marketplace add krkawzq/file-tools
codex plugin add file-tools@file-tools
```

</td>
    </tr>
    <tr>
      <td><strong>Claude Code</strong></td>
      <td>

```bash
claude plugin marketplace add krkawzq/file-tools
claude plugin install file-tools@file-tools
```

</td>
    </tr>
    <tr>
      <td><strong>Grok</strong></td>
      <td>

```bash
grok plugin marketplace add krkawzq/file-tools
grok plugin install file-tools --trust
```

</td>
    </tr>
    <tr>
      <td><strong>Cursor</strong></td>
      <td>Publish / install via Cursor marketplace or team catalog using this repo.</td>
    </tr>
  </tbody>
</table>

Plugins share the same skills and MCP tools; only the host manifest differs.

After installing or updating a plugin, invoke its bundled
`config-file-tools` skill once. It resolves the host's active plugin cache,
installs the latest compatible `krkawzq/file-tools` release wheel and
`fastmcp` into that root's `.venv`, and verifies MCP discovery. Plugin installs
do not require a local Rust toolchain or Maturin build.

---

## Requirements

| | |
|---|---|
| **Python** | 3.12+ |
| **Runtime dep** | `fastmcp` (MCP) |
| **Native core** | Built via Maturin / shipped wheel (Rust) |
| **SSH** | System OpenSSH client |

From source:

```bash
uv sync --extra dev
uv run maturin develop --release --uv
uv run pytest
```

---

## Project map

```text
file-tools/
├── src/file_tools/     # Python API, MCP, CLI
├── src/core-rs/        # Native I/O & algorithms
├── skills/             # Agent skills (shared)
├── scripts/            # Portable MCP launcher
├── .mcp.json           # Project MCP entry
├── .codex-plugin/      # Codex
├── .claude-plugin/     # Claude Code
├── .grok-plugin/       # Grok
└── .cursor-plugin/     # Cursor
```

---

## License

[MIT](https://opensource.org/licenses/MIT) · [krkawzq/file-tools](https://github.com/krkawzq/file-tools)
