---
name: file-tools
description: >
  Agent-oriented file I/O and command execution tools via MCP.
  Provides read, write, edit, apply_patch, and bash with pluggable
  local/SSH backends and a Rust string core. Use this skill when
  you need filesystem access beyond the built-in Read/Write/Edit/Bash
  tools — especially for SSH remote operations, structured patch
  application, or when the project's .mcp.json registers this server.
---

# File Tools (MCP)

## Overview

This skill provides five MCP tools for agent file I/O and command execution:

| Tool | Purpose |
|---|---|
| `read` | Read a UTF-8 text file with 1-based line numbers, tail support, and line windows |
| `write` | Create or completely overwrite a UTF-8 text file |
| `edit` | Exact string replacement with unique-match enforcement, replace_all, and prepend |
| `apply_patch` | Apply a structured multi-file patch (add, update, delete, move) with preflight validation |
| `bash` | Execute a foreground shell command with timeout, env, stdin, and bounded output capture |

All five tools support a pluggable client backend: `local` (default) or `ssh`. The Rust core handles edit matching, line slicing, and patch application at native speed.

## When to Use These Tools

Use the file-tools MCP tools when:

- **SSH remote operations** — You need to read, write, edit, or run commands on a remote machine via SSH. The built-in tools only work locally.
- **Structured patch application** — You have a multi-file patch with add/update/delete/move hunks that must be preflighted and applied atomically.
- **Edit with uniqueness guarantees** — You need `edit` to enforce single-match semantics (fail on 0 or >1 matches) rather than silent wrong matches.
- **Tail reads** — You need to read the last N lines of a file (`offset=-50`).
- **This project registers the server** — The project's `.mcp.json` includes the `file-tools` MCP server; use its tools when they are available.

### When *not* to use these tools

- For simple local file reads/writes/edits where the built-in `Read`/`Write`/`Edit` tools suffice — prefer the built-ins for speed and simplicity.
- When the MCP server is not running or not registered in the current project.

## Common Parameters (All Tools)

Every tool accepts these client-selection parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `cwd` | string | **required** | Working directory on the selected client. Relative paths resolve against this. |
| `client` | `"local"` \| `"ssh"` | `"local"` | Filesystem/execution backend. |
| `ssh_host` | string | `""` | SSH hostname or IP. Required when `client="ssh"`. |
| `ssh_port` | int | — | SSH port. Required as a positive integer when `client="ssh"`. No implicit port 22. |
| `ssh_user` | string | `""` | SSH username. Required when `client="ssh"`. |
| `ssh_password` | string | `""` | Optional explicit password. Key auth is preferred. |
| `ssh_key` | string | `""` | Optional private-key path on the host filesystem. |
| `ssh_flags` | string | `""` | Supported OpenSSH-style flags: `-X`, `-Y`, `-A`, `-a`, `-C`. |
| `ssh_accept_unknown_host_key` | bool | `false` | Insecure opt-in to trust an unknown host key. |

---

## Tool Reference

### 1. `read` — Read a file

```
read(target_file, cwd, offset=1, limit=2000, show_line_numbers=True, ...)
```

**Line selection rules:**

- `offset >= 1` — start at that 1-based line. `offset=0` is an alias for line 1.
- `offset < 0` — return the last `abs(offset)` lines (tail). `limit` is ignored.
- `offset` beyond EOF → empty string.
- `limit` caps lines only when `offset` is non-negative. Must be > 0.

**Line numbers:** When `show_line_numbers=True` (default), output is `cat -n` style with a truncation notice when the file exceeds the read window. Set to `false` for exact source text.

**Constraints:** Target must be a non-empty regular file. Directories and special files are rejected. UTF-8 decode errors are replaced.

```
# Read first 50 lines with line numbers
read("src/main.py", cwd="/project", limit=50)

# Read last 20 lines
read("logs/app.log", cwd="/project", offset=-20)

# Read exact content (no line numbers)
read("config.json", cwd="/project", show_line_numbers=False)
```

### 2. `write` — Create or overwrite a file

```
write(file_path, content, cwd, ...)
```

**Behavior:**

- Creates missing parent directories automatically.
- If the target exists and is a regular file, **all existing contents are replaced** — no read-before-write, no backup.
- `content` is written exactly as supplied: no trailing newline is added. An empty string creates/truncates to zero bytes.
- The target cannot be an existing directory.

**Warning:** This is destructive for existing files. Read the file first if any current contents must be preserved.

```
write("notes/todo.txt", "buy milk\n", cwd="/project")
```

### 3. `edit` — Exact string replacement

```
edit(file_path, old_string, new_string, cwd, replace_all=False, prepend=False, ...)
```

**Matching rules (in order):**

1. Exact substring match against file content (character-for-character, including indentation and trailing whitespace).
2. If exact match fails, retries with per-line trailing whitespace ignored (robust against invisible trailing spaces).

**Default behavior (replace_all=False):**

- `old_string` must match **exactly once**. Zero matches → error. Multiple matches → ambiguity error with context snippets.
- Include enough surrounding context to make the match unique.

**replace_all=True:** Replace every non-overlapping occurrence.

**Special cases:**

- `old_string=""` → create a new file with `new_string` as content. Fails if target already exists. Parent directories are created.
- `old_string=""` + `prepend=True` → prepend `new_string` to an existing file. Target must exist.
- `new_string=""` → delete the matched text.
- **No `append` operation** — use `apply_patch` with an add-only chunk at EOF instead.

**EOF newline:** `edit` never adds a trailing newline automatically. A normal replacement preserves the existing EOF newline unless the matched or replacement text explicitly changes it.

```
# Single replacement
edit("app.py", "DEBUG = True", "DEBUG = False", cwd="/project")

# Replace all occurrences
edit("config.ini", "localhost", "0.0.0.0", cwd="/project", replace_all=True)

# Create a new file
edit("new_module.py", "", "# New module\n", cwd="/project")
```

### 4. `apply_patch` — Structured multi-file patch

```
apply_patch(patch_text, cwd, ...)
```

**Format:** A structured patch document (not a unified diff). Control markers start in column 1.

```
*** Begin Patch
*** Update File: settings.txt
@@
 [theme]
-color=blue
+color=green
*** End Patch
```

**Operations:**

| Marker | Purpose |
|---|---|
| `*** Begin Patch` | Required first line |
| `*** Update File: path` | Modify an existing regular file |
| `*** Add File: path` | Create a new file (content lines prefixed with `+`) |
| `*** Delete File: path` | Delete an existing file (no content lines) |
| `*** Move to: dest` | Move/rename (inside Update File; destination must not exist) |
| `@@` or `@@ context` | Start an update chunk; context seeks to a literal line then inserts after it |
| `*** End of File` | Require old lines to match at EOF |
| `*** End Patch` | Required last line |

**Content line prefixes inside Update File:**
- ` ` (space) — unchanged context line
- `-` — line to remove
- `+` — line to add

**Preflight:** The complete patch is parsed and validated against an in-memory filesystem view before writes begin. Syntax errors, missing sources, conflicting destinations, and unmatched context all prevent mutation.

**Chunk matching:** Tries exact lines first, then tolerates trailing whitespace, full trim, and normalized Unicode. Chunks run in order; later searches start after earlier matches. An add-only chunk without named context inserts at EOF.

```
# Insert after a specific line
*** Begin Patch
*** Update File: app.py
@@ def main():
+    log_startup()
*** End Patch

# EOF-anchored replacement
*** Begin Patch
*** Update File: app.py
@@
-raise SystemExit(main())
+raise SystemExit(run())
*** End of File
*** End Patch

# Move a file
*** Begin Patch
*** Update File: old_name.py
*** Move to: new_name.py
*** End Patch
```

### 5. `bash` — Execute a shell command

```
bash(command, cwd, timeout=120.0, description="", interpreter="auto", flags="", env=None, stdin=None, max_output_bytes=1048576, ...)
```

**Behavior:**

- Foreground, non-interactive execution. No reusable terminal session or PTY.
- `interpreter="auto"` selects `cmd` on local Windows, `bash` elsewhere.
- Shell syntax (pipes, redirects, `&&`, `||`, `$()`, background `&`) all work.
- **No sandbox, approval gate, or command filtering** — commands run with the selected client's permissions.

**Output capture:** Each stream (stdout/stderr) retains up to `max_output_bytes` (default 1 MiB, max 16 MiB). Formatted output caps at 100,000 characters (head+tail preserved with truncation marker).

**Timeout:** Default 120s. `0` disables. On timeout, returns exit code 124 with `timed_out` marker and captured partial output (no exception).

**Environment:** `env` is a dict of NAME=VALUE overrides for the child process. Names must match `[A-Za-z_][A-Za-z0-9_]*`.

**Known interpreters receive their command-string flag automatically:** `-c` for POSIX shells/Python/Ruby/Perl, `-Command` for PowerShell, `/c` for `cmd`. Supply only additional flags in `flags`.

```
# Simple command
bash("git status --short", cwd="/project", description="check repo status")

# With timeout and env
bash("python train.py", cwd="/project", timeout=3600,
     env={"CUDA_VISIBLE_DEVICES": "0"}, description="training run")

# With stdin
bash("python -c 'import sys; print(sys.stdin.read().upper())'",
     cwd="/project", stdin="hello world")
```

---

## SSH Usage Pattern

When operating on a remote machine (e.g., a GPU worker pod), pass SSH parameters:

```
read("/workspace/config.yaml", cwd="/workspace",
     client="ssh", ssh_host="worker.pod.example.com",
     ssh_port=2222, ssh_user="wangzhongqi")
```

**Key points:**
- `cwd` is the working directory **on the remote machine**.
- SSH auth uses paramiko in-process: password, key file, or TTY fallback.
- Host keys are verified against the standard `known_hosts` files; unknown keys are rejected unless `ssh_accept_unknown_host_key=True`.
- `edit` and `apply_patch` operate on the remote filesystem through the SSH client — same semantics as local.

---

## Interaction with Built-in Tools

Claude Code ships with built-in `Read`, `Write`, `Edit`, and `Bash` tools. The file-tools MCP tools are **supplementary**, not replacements:

| Scenario | Use |
|---|---|
| Local file read/write/edit | Built-in tools (simpler, faster) |
| **SSH remote** file operations | **file-tools MCP** (built-ins can't SSH) |
| Structured multi-file **patch** | **file-tools MCP** (`apply_patch` has no built-in equivalent) |
| Edit with **uniqueness enforcement** | **file-tools MCP** (`edit` fails on ambiguous matches) |
| **Tail reads** (last N lines) | **file-tools MCP** (`read` with negative offset) |
| Local bash commands | Built-in `Bash` (simpler) |
| **SSH remote** command execution | **file-tools MCP** (`bash` with `client="ssh"`) |

**Rule of thumb:** Default to built-in tools for local operations. Use file-tools MCP tools when you need SSH access, structured patches, uniqueness-guaranteed edits, or tail reads.
