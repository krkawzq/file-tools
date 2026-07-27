---
name: file-tools
description: Prefer the system's built-in file reading, editing, and terminal tools for ordinary local work. Load the File Tools MCP skill only when the user explicitly asks to load or use it, or for sustained SSH-backed work such as remote file reads or edits and longer-running but bounded foreground commands. Do not trigger this skill merely because a task involves files or shell commands.
---

# File Tools

Use the File Tools MCP tools for filesystem and foreground command work in an explicitly selected workspace. Always pass `cwd`; never infer a remote working directory.

## Follow the core workflow

1. Establish the target workspace and choose `client="local"` or `client="ssh"`.
2. Inspect the relevant files before preserving or changing existing content.
3. Choose the least destructive tool that expresses the intended operation.
4. Review changed files or run a bounded verification command before reporting completion.

## Choose the operation

- Use `read` for UTF-8 text, line windows, or tail reads with a negative `offset`. Set `show_line_numbers=false` when exact source text is needed for a later edit or comparison. Continue from the next offset when a read is truncated.
- Use `edit` for a unique literal replacement, new-file creation with `old_string=""`, or explicit prepend. Matching tries exact text first and then ignores trailing whitespace per line. If matching returns zero or multiple occurrences, read a narrower region and retry with more context; do not guess. Use `replace_all=true` only when every occurrence should change.
- Use `apply_patch` for coordinated add, update, delete, move, append, or multi-file changes. Pass the structured patch directly, without Markdown fences or unified-diff headers. The whole patch is preflighted before writes begin, but low-level write failures are not transactionally rolled back.
- Use `write` only when complete replacement is intended. It creates missing parent directories, overwrites an existing regular file without a backup, and does not add a trailing newline automatically.
- Use `bash` for foreground, non-interactive command execution. Set a finite timeout for commands that may wait or recurse, bound retained output, and provide `stdin` explicitly when needed. It does not provide a reusable shell, PTY, sandbox, approval gate, or background-task manager.

## Select the client

- For local work, set `client="local"` and use the target workspace as `cwd`.
- For remote work, set `client="ssh"` and pass `ssh_host`, a positive `ssh_port`, `ssh_user`, and the remote `cwd`. There is no implicit port 22 fallback.
- Prefer key-based SSH authentication. Do not place passwords, private-key contents, or other secrets in messages, patches, command text, or committed files.
- Keep unknown-host-key acceptance disabled unless the user explicitly accepts the security tradeoff.
- Treat remote paths as paths on the SSH host. Confirm the remote workspace instead of reusing a local-only path assumption.

## Preserve data and execution control

- Read before modifying whenever existing content must be preserved; treat `write` and patch deletions as destructive.
- Keep edits scoped to the selected workspace and the user's request. Resolve exact targets before overwriting, deleting, or moving files.
- Review the affected files after `edit`, `write`, or `apply_patch`; do not equate a successful tool call with semantic correctness.
- Treat `bash` as arbitrary code execution with the selected client's permissions. Do not run destructive commands or intentionally detached processes without explicit user intent and a clear lifecycle owner.
- On timeout, inspect the returned exit status and partial output. Do not silently retry with an unbounded timeout.

## Use alongside host-native tools

Prefer the host's built-in file and terminal tools by default. Load and use File Tools only when the user explicitly requests it or when sustained SSH-backed work requires remote file reads, edits, patches, or longer-running but bounded foreground commands. Do not select File Tools for ordinary local work solely because it offers different matching or line-reading semantics. If the MCP server is unavailable, report that limitation rather than implying that File Tools performed the work.
