# file-tools

Agent-oriented file tools with:

- **Pluggable terminal clients** (`local`, `ssh`) — file I/O + command execution
- **Rust string core** (pyo3) for edit matching, line slicing, and patch application
- **Agent protocol**: 1-based line offsets; negative offset = tail
- **MCP**: `tools.py` (scalar wrappers) + `server.py` (server build only)
- **CLI**: MCP-aligned `read`, `write`, `apply_patch`, and `bash` commands

## Layout

```text
src/
  core-rs/           # Rust pyo3 core
  file_tools/
    client/          # LocalClient, SshClient, client factory/cache
    tools/           # exactly read / write / edit / apply_patch / bash
    mcp/
      tools.py       # FastMCP tool registration (simple params only)
      server.py      # create_mcp_server()
    cli/
      tools.py       # scalar CLI adapters + exit-code handling
      main.py        # argparse parser and console entry point
    _core.pyi
  tests/
```

## Build

```bash
uv sync --extra dev
uv run maturin develop --release --uv
uv run pytest
cargo test --no-default-features
```

Maturin builds a CPython 3.12+ stable-ABI wheel (`abi3`), so one wheel per
operating-system/architecture target supports every declared CPython version.
CI builds and tests on Linux, Windows, and macOS with Python 3.12–3.14.

The performance regression checks use small in-memory inputs and can be run
separately:

POSIX shells:

```sh
uv run pytest -m performance --durations=0
```

## Usage

```python
from file_tools import LocalClient, apply_patch, edit, read, write, get_client

client = LocalClient(cwd="/tmp/project")

# 1-based offset; -10 = last 10 lines
r = read("src/main.py", offset=1, limit=50, client=client)

# Or resolve from simple params (LRU-cached)
c = get_client(client_type="local", cwd="/tmp/project")
edit("src/main.py", "old", "new", client=c)
```

### SSH

```python
from file_tools import get_client, read

client = get_client(
    client="ssh",
    ssh_host="host.example.com",
    ssh_port=22,
    ssh_user="user",
    cwd="/home/user/proj",
)
print(read("README.md", client=client).content)
```

### MCP client params (all tools)

| param | default | meaning |
|---|---|---|
| `cwd` | **required** | working directory |
| `client` | `"local"` | `"local"` or `"ssh"` |
| `ssh_host` | — | **required** when `client=ssh` |
| `ssh_port` | — | **required** when `client=ssh` |
| `ssh_user` | — | **required** when `client=ssh` |
| `ssh_password` | `""` | optional explicit password |
| `ssh_key` | `""` | optional private key path |
| `ssh_flags` | `""` | e.g. `"-X -A"` |
| `ssh_accept_unknown_host_key` | `false` | insecure opt-in for a key absent from `known_hosts` |

Auth is in-process via paramiko. Interactive `getpass` is a TTY-only fallback
when no password is set. SSH host keys are loaded from the server account's
standard `known_hosts` files and unknown keys are rejected by default.


MCP layering: plain functions in `mcp/tools.py`; thin `@mcp.tool` wrappers in `mcp/server.py`.

## CLI

The CLI mirrors the MCP scalar parameters except that `edit` is intentionally
not exposed. `write` and `apply_patch` read their complete input from stdin.

```bash
# Read lines 1-50 without line-number prefixes.
file-tools read README.md --cwd "$PWD" --limit 50 --no-show-line-numbers

# Create or completely overwrite a file with stdin.
printf 'hello\n' | file-tools write notes/hello.txt --cwd "$PWD"

# Apply a complete Codex patch from stdin.
file-tools apply_patch --cwd "$PWD" < changes.patch

# Execute a command; the file-tools process returns the command exit code.
file-tools bash 'git status --short' --cwd "$PWD"
```

PowerShell:

```powershell
file-tools read README.md --cwd $PWD.Path --limit 50 --no-show-line-numbers
"hello`n" | file-tools write notes/hello.txt --cwd $PWD.Path
Get-Content -Raw changes.patch | file-tools apply_patch --cwd $PWD.Path
file-tools bash "git status --short" --cwd $PWD.Path
```

Every command accepts `--client local|ssh` and the same `--ssh-host`,
`--ssh-port`, `--ssh-user`, `--ssh-password`, `--ssh-key`, and `--ssh-flags`
connection parameters as MCP. Run `file-tools <command> --help` for the full
per-command interface.


### Bash tool

```python
from pathlib import Path

from file_tools import bash, LocalClient

project_dir = Path.cwd()
r = bash(
    "git status --short",
    cwd=project_dir,             # required
    timeout=120,
    description="inspect repo",
    max_output_bytes=1024 * 1024,  # retained independently per stream
    client=LocalClient(cwd=project_dir),
)
print(r.format())  # agent-facing text
# auto: cmd /c on local Windows; bash -c on Linux, macOS, and SSH
```

The default interpreter is `auto`. Shell syntax is therefore platform-specific:
use `cmd` syntax on local Windows and `bash` syntax on Linux/macOS or SSH, or
pass an explicit `interpreter` such as `powershell`, `pwsh`, `python`, or
`bash`. Windows-style interpreter flags preserve backslashes in paths.

Command output is drained incrementally. Each stream retains a bounded head and
tail (1 MiB by default, at most 16 MiB), reports omitted/total byte counts, and
decodes invalid UTF-8 with replacement characters. Local timeouts terminate the
whole process group with TERM→KILL escalation; SSH execution uses a remote
PID/process-group wrapper for best-effort descendant cleanup.

No sandbox, command-content filtering, background-operator rejection, or
approval gates are applied. Background operators are passed to the shell
unchanged; this package does not provide a separate background-task manager.
Use a finite timeout and do not launch an intentionally detached process unless
another system owns its lifecycle.

## Platform behavior

- Local paths use the host platform's native `pathlib` semantics, including
  Windows drive and UNC paths. SSH paths always use remote POSIX semantics.
- `read`, `edit`, and `apply_patch` preserve existing CRLF files. LF-form edit
  and patch input can match CRLF content without creating mixed line endings.
- `edit` writes literal replacement text without forcing an EOF newline;
  move-only patches preserve the source file's exact EOF state as well.
- Local timeout cleanup uses POSIX process groups on Linux/macOS and Windows
  process groups plus `taskkill` when available.
- CLI stdin/stdout uses underlying byte streams when available, avoiding
  universal-newline conversion and doubled CRLF output on Windows.

## Design notes

- Tools own read/write orchestration; all path I/O goes through `Client`.
- `client/factory.py` creates and LRU-caches clients from scalar params.
- Hot string paths: Rust `file_tools._core`.
- No sandbox / no read-before-write enforcement.
