---
name: config-file-tools
description: Install File Tools into Codex, Claude Code, Grok, or Cursor by downloading the latest krkawzq/file-tools GitHub Release wheel into that host's enabled plugin .venv, then use the host-specific plugin-root path and venv Python to verify all five MCP tools. Trigger on fresh setup, marketplace/plugin installation, host migration, plugin update, missing .venv, failed import of file_tools._core, fastmcp missing, launcher/interpreter mismatch, or MCP not exposing all five tools.
---

# Configure File Tools

Bring File Tools up on **one selected host** by:

1. Installing / locating that host's **enabled** plugin copy
2. Downloading the latest compatible **GitHub Release** wheel for `krkawzq/file-tools`
3. Installing the wheel into **that plugin root's `.venv`**
4. Using the committed host-specific plugin-root path and `.venv` Python
5. Verifying imports and MCP list-tools

Do **not** compile with Maturin for marketplace/plugin installs. Do **not** install into a nearby source checkout and expect the host to pick it up.

Ask which host to configure when unclear. Teach and execute **only that host's** commands; do not mix Codex / Claude / Grok / Cursor paths.

## Shared pipeline (all hosts)

After `$FILE_TOOLS_ROOT` is proven for the selected host:

### 1) Install the latest release into plugin `.venv`

Run the bundled installer from the proven installed root:

```bash
python3 "$FILE_TOOLS_ROOT/skills/config-file-tools/scripts/install_latest_release.py" \
  --plugin-root "$FILE_TOOLS_ROOT"
```

The installer queries GitHub's latest published release for
`krkawzq/file-tools`, selects the current OS, CPU, and libc's `cp312-abi3`
wheel, creates `$FILE_TOOLS_ROOT/.venv`, explicitly installs both the wheel and
`fastmcp>=3.4.4`, and verifies native import plus MCP server construction.

It prefers `uv venv` and `uv pip install`. When `uv` is absent, it finds Python
3.12+, creates the environment with `venv`, bootstraps `pip` with `ensurepip`,
and installs the wheel and `fastmcp` with that environment's Python.

If GitHub has no published release or the latest release has no compatible
wheel, stop and report the exact condition and available wheel assets. Do not
compile with Maturin/Cargo or install a source checkout as a fallback.

After a plugin update, re-resolve the enabled root and rerun the installer:
hosts commonly activate a new cache directory. Keep `.venv` inside that root;
never reuse another root's venv or copy native modules across OS/architecture.

### 2) Use the committed host launcher

The distributable manifests use the plugin root plus its local venv:

| Host | Effective MCP command |
|---|---|
| Claude Code | `${CLAUDE_PLUGIN_ROOT}/.venv/bin/python -m file_tools.cli.mcp_server` |
| Codex | `./.venv/bin/python -m file_tools.cli.mcp_server` with `cwd: "."`; Codex resolves the relative cwd to the installed plugin root |
| Cursor | `${CURSOR_PLUGIN_ROOT}/.venv/bin/python -m file_tools.cli.mcp_server` |
| Grok | `${GROK_PLUGIN_ROOT}/.venv/bin/python -m file_tools.cli.mcp_server` |

Do not replace these with a machine-specific cache path. Do not restore
`PYTHONPATH=./src`: the released package and native extension must both load
from the plugin `.venv`. On Windows, the installer creates
`.venv/bin/python.exe` as a compatibility entry for the native
`.venv/Scripts/python.exe`, so the same manifest command remains valid.

### 3) Verify

```bash
cd "$FILE_TOOLS_ROOT"
.venv/bin/python -c "from file_tools._core import count_lines; from file_tools import read, write, edit, apply_patch, bash; print('plugin venv imports OK')"
.venv/bin/file-tools --help
.venv/bin/python -c "from file_tools.cli.mcp_server import create_mcp_server; create_mcp_server(); print('server construction OK')"
```

On native Windows, use `.venv\Scripts\python.exe` for the Python checks and
`.venv\Scripts\file-tools.exe --help` for the console script.

Then exercise the **effective** host MCP entry (or stdio list-tools). Discovery must list exactly: `read`, `write`, `edit`, `apply_patch`, `bash`.

Restart or open a new agent thread when the host caches skills / MCP processes.

---

## Codex

**Identity:** `file-tools@file-tools` (marketplace name `file-tools`). Do not use `file-tools@personal` when GitHub was requested.

**Install / refresh**

```bash
codex plugin marketplace add krkawzq/file-tools          # only if missing
codex plugin marketplace upgrade file-tools              # when user wants latest marketplace snapshot
codex plugin add file-tools@file-tools --json
```

**Resolve `$FILE_TOOLS_ROOT`**

- Prefer `installedPath` from `codex plugin add … --json`.
- Already installed: `codex plugin list --json` and confirm `pluginId` is `file-tools@file-tools` with `installed` + `enabled`. Then take the path from a fresh `codex plugin add file-tools@file-tools --json` (idempotent path report) or the enabled cache dir under `~/.codex/plugins/cache/file-tools/file-tools/<version>/`.
- Confirm with `realpath`. Root must contain `.codex-plugin/plugin.json`, `.mcp.json`, `skills/`, `scripts/`.

**MCP launcher:** `.mcp.json` uses `command: "./.venv/bin/python"`,
`args: ["-m", "file_tools.cli.mcp_server"]`, and `cwd: "."`. Keep that
plugin-relative form; Codex resolves the cwd against the installed plugin root.

**Done when:** `codex plugin list` shows `file-tools@file-tools` enabled, venv imports pass, and MCP list-tools returns all five tools.

---

## Claude Code

**Identity:** `file-tools@file-tools`.

**Install / refresh**

```bash
claude plugin marketplace add krkawzq/file-tools         # only if missing
claude plugin marketplace update file-tools              # when user wants latest
claude plugin install file-tools@file-tools              # scope: user (default) / project / local via -s
```

**Resolve `$FILE_TOOLS_ROOT`**

- `claude plugin list --json` → entry `id` = `file-tools@file-tools`, `enabled` = true → use `installPath`.
- Fallback: `~/.claude/plugins/installed_plugins.json` → `plugins["file-tools@file-tools"][].installPath` for the active scope.
- Runtime expansion in manifests: `${CLAUDE_PLUGIN_ROOT}` (= that install path).
- Confirm with `realpath`. Root must contain `.claude-plugin/plugin.json`, `skills/`, `scripts/`.

**MCP launcher:** `.claude-plugin/plugin.json` uses
`${CLAUDE_PLUGIN_ROOT}/.venv/bin/python` with
`["-m", "file_tools.cli.mcp_server"]`.

**Done when:** plugin enabled at the intended scope, venv imports pass, MCP list-tools returns all five tools. Restart Claude Code after install/update when required.

---

## Grok

**Identity:** plugin name `file-tools`. MCP stays inactive until the plugin is **trusted**; skills load when **enabled**.

**Install / refresh**

```bash
grok plugin marketplace add krkawzq/file-tools           # only if missing
grok plugin marketplace update                           # when user wants latest sources
grok plugin install file-tools --trust                   # from the marketplace catalog
# equivalent direct install:
# grok plugin install krkawzq/file-tools --trust
grok plugin enable file-tools                            # if list shows installed but disabled
```

**Resolve `$FILE_TOOLS_ROOT`**

- `grok plugin list --json` and `grok plugin details file-tools` — confirm installed / enabled / trusted.
- Discover path from the Plugins UI path field, `grok inspect --json`, or the enabled copy under discovery roots (priority): session/`--plugin-dir`, project `.grok/plugins/`, user `~/.grok/plugins/`, then `[plugins].paths`.
- Runtime expansion: `${GROK_PLUGIN_ROOT}` (aliases `${CLAUDE_PLUGIN_ROOT}` may also be set).
- Confirm with `realpath`. Root must contain `.grok-plugin/plugin.json`, `skills/`, `scripts/`.

**MCP launcher:** `.grok-plugin/plugin.json` uses
`${GROK_PLUGIN_ROOT}/.venv/bin/python` with
`["-m", "file_tools.cli.mcp_server"]`. If MCP is blocked, reinstall with
`--trust` or use the host's trust workflow. Reload plugins or start a new
session after changes.

**Done when:** `grok plugin list` shows enabled, trust allows MCP, venv imports pass, MCP list-tools returns all five tools.

---

## Cursor

**Identity:** plugin `file-tools` with `.cursor-plugin/plugin.json`. Distribution is via **Cursor Marketplace / team catalog / Customize**, not the Codex/Claude/Grok CLI flow.

**Install / refresh**

- Install or update **file-tools** from Cursor Marketplace, team marketplace, or the host's Customize → Plugins UI using repo `krkawzq/file-tools`.
- There is no portable `cursor plugin add` equivalent to teach as the primary path; use the UI/catalog the user actually has.

**Resolve `$FILE_TOOLS_ROOT`**

- Find the enabled installed copy Cursor actually launches (often under `~/.cursor/plugins/cache/<marketplace-or-source>/file-tools/<version-or-sha>/`, or a team/local plugins path).
- Confirm the directory contains `.cursor-plugin/plugin.json` and `skills/`. Prefer host UI / plugin details over guessing; treat filesystem search as last resort and verify name, version, and that it is the enabled registration.
- Confirm with `realpath`. Do not substitute a development checkout unless Cursor is explicitly configured to use it.

**MCP launcher:** `.cursor-plugin/plugin.json` uses
`${CURSOR_PLUGIN_ROOT}/.venv/bin/python` with
`["-m", "file_tools.cli.mcp_server"]` and no source `PYTHONPATH`.

**Done when:** Cursor shows the plugin enabled, venv imports pass, and Agent/MCP list-tools returns all five tools. Reload the window or start a new agent chat after install/update when MCP is cached.

---

## Diagnose by layer

| Symptom | Likely cause and check |
|---|---|
| Latest-release request returns 404 | GitHub has no published release yet. Stop; do not compile in the plugin cache. |
| No matching release wheel | Latest release assets are missing for this OS/CPU/libc. |
| Plugin enabled but `_core` missing | Wheel never installed into the **enabled** root's `.venv`, or wrong root. |
| Checkout works, host fails | Host runs another cached root. Re-resolve that host's enabled path. |
| `.venv` works, host lacks `fastmcp` / `_core` | Effective host config is stale or points at a different plugin root. |
| Cursor imports wrong package | Leftover `PYTHONPATH=./src`. Drop it; use venv python/`file-tools-mcp`. |
| Grok MCP blocked | Plugin not trusted. `--trust` or `~/.grok/plugins/`. |
| Update breaks again | New version directory activated. Re-resolve it and rerun the bundled installer. |
| Server starts, tools missing | MCP list-tools required; do not stop at process start. |
| SSH fails only | Auth / keys / port / remote `cwd` — not an install issue. |

## Integrity

- One host per run; preserve that host's identity (`file-tools@file-tools` vs `file-tools`), manifests, and root variables.
- Install only compatible wheel assets from the latest published `krkawzq/file-tools` release.
- Keep `.venv` plugin-local; never copy native modules across roots/OS/arch.
- Do not commit machine-specific cache paths into distributable manifests or restore source `PYTHONPATH`.
- Do not weaken SSH host-key checks or embed credentials in smoke tests.
- Report host, plugin id, `$FILE_TOOLS_ROOT`, latest release tag, wheel asset, launcher used, verified layer, and remaining failure when setup cannot finish.
