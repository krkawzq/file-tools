---
name: config-file-tools
description: Install File Tools into Codex, Claude Code, Grok, or Cursor by downloading the matching GitHub Release wheel into that host's enabled plugin .venv, then pin the host MCP launcher to the venv and verify all five tools. Trigger on fresh setup, marketplace/plugin installation, host migration, plugin update, missing .venv, failed import of file_tools._core, fastmcp missing, launcher/interpreter mismatch, or MCP not exposing all five tools.
---

# Configure File Tools

Bring File Tools up on **one selected host** by:

1. Installing / locating that host's **enabled** plugin copy
2. Downloading the matching **GitHub Release** wheel for `krkawzq/file-tools`
3. Installing the wheel into **that plugin root's `.venv`**
4. Pinning the host MCP entry to the **venv entrypoint** (not bare `python` + `scripts/file_tools_mcp.py`)
5. Verifying imports and MCP list-tools

Do **not** compile with Maturin for marketplace/plugin installs. Do **not** install into a nearby source checkout and expect the host to pick it up.

Ask which host to configure when unclear. Teach and execute **only that host's** commands; do not mix Codex / Claude / Grok / Cursor paths.

## Shared pipeline (all hosts)

After `$FILE_TOOLS_ROOT` is proven for the selected host:

### 1) Version → Release → wheel

1. Read `version` from the host manifest inside `$FILE_TOOLS_ROOT` (see per-host table).
2. Normalize: strip any `+…` local suffix (`0.1.0+codex.…` → `0.1.0`).
3. Prefer tag `v<version>`; if missing, try `<version>`.
4. Download abi3 wheels for that tag from **`krkawzq/file-tools`**, keep only the asset for **this OS/CPU**:

| Platform | Prefer asset name containing |
|---|---|
| Linux x86_64 | `manylinux` + `x86_64` |
| Linux aarch64 | `manylinux` + `aarch64` / `arm64` |
| macOS arm64 | `macosx` + `arm64` / `universal2` |
| macOS x86_64 | `macosx` + `x86_64` / `universal2` |
| Windows AMD64 | `win_amd64` |

```bash
TAG="v0.1.0"   # normalized plugin version
WORKDIR="$(mktemp -d)"
gh release download "$TAG" -R krkawzq/file-tools --dir "$WORKDIR" --pattern 'file_tools-*.whl'
# Keep only the matching OS/arch wheel.
```

If the tag or matching wheel is missing, stop and report plugin version, attempted tag, platform, and available assets. Do not fall back to building from source unless the user explicitly asks.

### 2) Install into plugin `.venv`

```bash
cd "$FILE_TOOLS_ROOT"
uv venv .venv --python 3.12
uv pip install --python .venv "$WORKDIR"/file_tools-*-cp312-abi3-*.whl   # the one matching this machine
```

- `.venv` must live in `$FILE_TOOLS_ROOT` only.
- The wheel must pull runtime deps (including `fastmcp`).
- After plugin updates, hosts often activate a **new** directory: re-resolve `$FILE_TOOLS_ROOT`, then repeat.

### 3) Why the launcher must change

Shipping manifests often still say bare `python` + `scripts/file_tools_mcp.py`. That script prepends `<root>/src` on `sys.path`, which can shadow the wheel install and miss `file_tools._core`.

For a Release-wheel setup, the effective MCP command must be the plugin venv entrypoint:

| OS | Preferred command |
|---|---|
| POSIX | `$FILE_TOOLS_ROOT/.venv/bin/file-tools-mcp` |
| POSIX (equiv.) | `$FILE_TOOLS_ROOT/.venv/bin/python` with args `-m` `file_tools.cli.mcp_server` |
| Windows | `$FILE_TOOLS_ROOT\.venv\Scripts\file-tools-mcp.exe` |

Apply this as a **machine-local** host override / user MCP config. Do **not** commit a machine-specific cache path into the distributable plugin manifest. Re-resolve the absolute path after every plugin update.

### 4) Verify

```bash
cd "$FILE_TOOLS_ROOT"
.venv/bin/python -c "from file_tools._core import count_lines; from file_tools import read, write, edit, apply_patch, bash; print('plugin venv imports OK')"
.venv/bin/file-tools --help
.venv/bin/python -c "from file_tools.cli.mcp_server import create_mcp_server; create_mcp_server(); print('server construction OK')"
```

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

**Version file:** `.codex-plugin/plugin.json` → `version` (may include `+codex.…`; strip for the Release tag).

**Stock MCP (do not rely on as-is):** `.mcp.json` → `command: python`, `args: ["./scripts/file_tools_mcp.py"]` with plugin-root cwd.

**Machine-local pin:** point Codex's effective MCP for this plugin at absolute `.venv/bin/file-tools-mcp` (or `.venv/bin/python -m file_tools.cli.mcp_server`). Keep the override outside the published manifest.

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

**Version file:** `.claude-plugin/plugin.json` → `version`.

**Stock MCP (do not rely on as-is):** `.claude-plugin/plugin.json` → `python ${CLAUDE_PLUGIN_ROOT}/scripts/file_tools_mcp.py`.

**Machine-local pin:** override the MCP `command` to absolute `$FILE_TOOLS_ROOT/.venv/bin/file-tools-mcp` (or venv `python` + `-m file_tools.cli.mcp_server`). Do not leave bare `python` if it is not the plugin venv.

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

**Version file:** `.grok-plugin/plugin.json` → `version`.

**Stock MCP (do not rely on as-is):** `.grok-plugin/plugin.json` → `python ${GROK_PLUGIN_ROOT}/scripts/file_tools_mcp.py`.

**Machine-local pin:** override to absolute `$FILE_TOOLS_ROOT/.venv/bin/file-tools-mcp` (or venv python `-m file_tools.cli.mcp_server`). If MCP is blocked, reinstall with `--trust` or place under `~/.grok/plugins/` (auto-trusted). Reload plugins (`r` in Plugins tab) or start a new session after changes.

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

**Version file:** `.cursor-plugin/plugin.json` → `version`.

**Stock MCP (do not rely on as-is):** `.cursor-plugin/plugin.json` inline server:

- `command: python`
- `args: ["-m", "file_tools.cli.mcp_server"]`
- `cwd: "."` (plugin root)
- `env.PYTHONPATH: "./src"` ← wrong for a wheel-in-`.venv` install; do not keep this once the venv is authoritative

**Machine-local pin:**

- `command`: absolute `$FILE_TOOLS_ROOT/.venv/bin/python` (Windows: `…\.venv\Scripts\python.exe`)
- `args`: `["-m", "file_tools.cli.mcp_server"]`
- `cwd`: plugin root (or omit if using `file-tools-mcp` console script)
- Remove `PYTHONPATH=./src` for the wheel install (package lives in the venv site-packages).

Alternatively use absolute `.venv/bin/file-tools-mcp` as `command` with empty/no module args.

**Done when:** Cursor shows the plugin enabled, venv imports pass, and Agent/MCP list-tools returns all five tools. Reload the window or start a new agent chat after install/update when MCP is cached.

---

## Diagnose by layer

| Symptom | Likely cause and check |
|---|---|
| No matching release / wheel | Wrong tag vs plugin version, or assets missing for this OS/arch. |
| Plugin enabled but `_core` missing | Wheel never installed into the **enabled** root's `.venv`, or wrong root. |
| Checkout works, host fails | Host runs another cached root. Re-resolve that host's enabled path. |
| `.venv` works, host lacks `fastmcp` / `_core` | Still on bare `python` or `scripts/file_tools_mcp.py` + `src` shadow. Pin venv entrypoint. |
| Cursor imports wrong package | Leftover `PYTHONPATH=./src`. Drop it; use venv python/`file-tools-mcp`. |
| Grok MCP blocked | Plugin not trusted. `--trust` or `~/.grok/plugins/`. |
| Update breaks again | New version directory activated. Re-resolve, re-download, recreate `.venv`, re-pin absolute launcher. |
| Server starts, tools missing | MCP list-tools required; do not stop at process start. |
| SSH fails only | Auth / keys / port / remote `cwd` — not an install issue. |

## Integrity

- One host per run; preserve that host's identity (`file-tools@file-tools` vs `file-tools`), manifests, and root variables.
- Install only Release assets from `krkawzq/file-tools` that match the plugin version and this platform.
- Keep `.venv` plugin-local; never copy native modules across roots/OS/arch.
- Do not commit machine-specific cache paths into distributable manifests.
- Do not weaken SSH host-key checks or embed credentials in smoke tests.
- Report host, plugin id, `$FILE_TOOLS_ROOT`, release tag, wheel asset, launcher used, verified layer, and remaining failure when setup cannot finish.
