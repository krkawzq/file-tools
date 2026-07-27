---
name: file-tools
description: Use the File Tools MCP server to read, write, edit, patch, or run bounded commands in an explicitly selected local or SSH-backed working directory. Trigger when the user requests filesystem work through File Tools, needs the same operation on a remote SSH workspace, or supplies File Tools connection parameters.
---

# File Tools

Use the `file-tools` MCP tools. Always pass an explicit `cwd`; never infer a remote working directory.

## Select an operation

- Use `read` to inspect UTF-8 text. Disable line numbers when exact file content is required.
- Use `edit` for a unique literal replacement, creation with an empty `old_string`, or explicit prepend.
- Use `apply_patch` for structured multi-file changes and appends.
- Use `write` only when replacing the complete destination content is intended.
- Use `bash` for bounded command execution. Set a finite timeout for operations that may wait or recurse.

## Select a client

- For local work, set `client="local"` and pass the target workspace as `cwd`.
- For remote work, set `client="ssh"` and pass `ssh_host`, `ssh_port`, `ssh_user`, and the remote `cwd`.
- Prefer key-based SSH authentication. Do not place passwords or private-key contents in messages or files.
- Keep unknown-host-key acceptance disabled unless the user explicitly accepts that security tradeoff.

## Preserve data

- Read before modifying when existing content must be preserved.
- Treat `write` as destructive replacement.
- Do not guess when `edit` reports zero or multiple matches; read a narrower region and retry with more context.
- Review the affected files after edits or patches.
- Treat `bash` as arbitrary code execution with the selected client's permissions. Do not run destructive or detached commands without explicit user intent.
