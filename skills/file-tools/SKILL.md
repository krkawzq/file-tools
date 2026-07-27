---
name: file-tools
description: Prefer the host's built-in Read / Write / Edit / Bash for ordinary local work. Use File Tools MCP when the user explicitly asks for it, or for sustained SSH-backed file ops and bounded foreground commands. Do not trigger merely because a task involves files or shell.
---

# File Tools

Use the File Tools MCP tools — `read`, `write`, `edit`, `apply_patch`, `bash` — for filesystem and foreground command work in an **explicit** workspace. Always pass `cwd`. Never infer a remote working directory from local paths or chat context.

Agent-facing contracts are on the MCP tool docstrings; follow those details for parameters and formats. This skill covers selection, sequencing, and safety.

## When to use

| Situation | Action |
|---|---|
| Ordinary local files / shell | Host built-ins first |
| User explicitly asks for File Tools | Use these MCP tools |
| Sustained **SSH** reads/edits/patches/commands | File Tools with `client="ssh"` |
| Need uniqueness-checked edit, structured multi-file patch, or bounded remote shell | File Tools |

If the MCP server is unavailable, say so — do not imply File Tools ran.

## Core workflow

1. Fix **workspace + client**: `client="local"` or `client="ssh"`, always with `cwd`.
2. **Read** before changing anything that must be preserved.
3. Pick the **least destructive** tool that expresses the intent.
4. After writes, **re-read** or run a bounded check before claiming done.

## Choose the tool

| Need | Tool |
|---|---|
| Inspect text / logs / source windows | `read` |
| One unique literal replace, create file, or prepend | `edit` |
| Multi-file change, move, delete, EOF append, structured hunks | `apply_patch` |
| Full-file body create or replace | `write` |
| Diagnostics, builds, tests, other shell | `bash` |

### `read`

- Defaults: `offset=1`, `limit=2000`, `show_line_numbers=true`.
- Negative `offset` → tail last N lines (`limit` ignored for window size).
- Empty files and non-regular paths error.
- For a later `edit`, set **`show_line_numbers=false`** so prefixes are not copied into `old_string`.
- If truncated, continue from the next 1-based line after the window.

### `edit`

Three modes only:

1. **Replace** — unique match by default; exact text first, then per-line trailing-whitespace tolerance. On 0 or many matches: narrow the region, add context, retry — do not guess. `replace_all=true` only when every occurrence should change.
2. **Create** — `old_string=""`; fails if the path already exists.
3. **Prepend** — `old_string=""` + `prepend=true` on an existing file.

No append parameter. Append at EOF via `apply_patch` (bare `@@`, only `+` lines). No auto trailing newline.

### `apply_patch`

- Body only: `*** Begin Patch` … `*** End Patch`. No Markdown fences, no unified-diff headers.
- Preflighted; low-level commit failures → best-effort deterministic rollback.
- Control markers in column 1; Update content lines need space / `-` / `+` prefixes.
- **Append at EOF**: `*** Update File` + bare `@@` + only `+` lines.
- **Move**: `*** Update File` + `*** Move to: dest` (dest must not exist).
- Delete/move are destructive after destination writes succeed.

### `write`

- Full-file create or replace only. Missing parents created; existing regular files overwritten with no backup.
- Prefer `edit` / `apply_patch` when any prior content must be kept.

### `bash`

- Foreground only. Default timeout **120s** (`0` disables). Timeout → exit **124** + partial output.
- Full shell semantics; no sandbox, PTY, reusable session, or background-task manager.
- Prefer file tools for mutations; use `bash` for run/check/build/test.
- Bound output with `max_output_bytes` when needed; pass `stdin` explicitly when required.

## Client and SSH

- **Local**: `client="local"`, workspace as `cwd`.
- **SSH**: `client="ssh"` + `ssh_host` + positive `ssh_port` + `ssh_user` + remote `cwd`. **No implicit port 22.**
- Prefer keys/agent. Do not put passwords, private keys, or secrets in messages, patches, command text, or commits.
- Leave `ssh_accept_unknown_host_key` false unless the user accepts that risk.
- Remote paths are on the SSH host — confirm remote `cwd` separately from local paths.

## Safety

- Treat `write`, patch delete/move, and `bash` as high-impact.
- Scope work to the selected workspace and the user's request.
- Tool success ≠ semantic correctness; verify after edits.
- On timeout, inspect exit + partial output; do not silently retry unbounded.
