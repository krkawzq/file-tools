# grep.py 实现计划

## 参考来源结论

调研了三处参考实现：

1. **codex (`codex-rs/file-search/src/lib.rs`)** — 文件**名**模糊搜索（nucleo 模糊匹配 + ignore crate 遍历），不是内容 grep。
2. **codex shell 层** — 真正的内容搜索靠外部 `rg`（ripgrep），`parse_command.rs` 里把 `rg` 当普通 shell 命令解析。
3. **grok (`search_tool`)** — MCP 工具发现（BM25 关键词检索 MCP server 工具清单），与文件内容搜索无关。

结论：**codex/grok 都没有把内容 grep 做成原生工具**，都委托给外部 `rg`。真正把 grep 当作一等公民、纯进程内实现的是 **Claude Code 自己的 Grep 工具**（见本会话系统提示中的 Grep 工具定义）。因此 `grep.py` 以 Claude Code Grep 的语义为准，纯 Python（`re` + `os.walk`/`pathlib`）实现，不依赖外部 `rg` 二进制——这样在开发盒/pod 上无需保证 `rg` 可用，也与项目已有的 `read.py`/`edit.py`/`write.py` 纯 Python 风格一致。

## 可借鉴的点

- 从 codex `file-search` 借鉴：`.gitignore` 尊重语义（`require_git`：仅当目录在 git repo 内才应用 gitignore，避免上层 `~/.gitignore` 误伤）；按 score 排序后取 top-N limit 的截断提示。
- 从 Claude Code Grep 工具定义借鉴：完整的参数集与 output_mode 语义。

## 文件结构

`src/grep.py`，单一文件，沿用项目既有风格：
- 顶部 docstring（中文，列出核心语义）
- `from __future__ import annotations`
- 常量区
- 异常类型区（`GrepError` 基类 + 子类）
- 结果 dataclass 区（`GrepMatch`、`GrepFileResult`、`GrepResult`）
- 内部辅助函数区
- 核心函数 `grep(...)`
- 便捷函数 `grep_files(...)`（只返回匹配文件路径列表的语法糖）
- `if __name__ == "__main__":` 内嵌自测（与 read.py/edit.py 一致）

## 核心函数签名

```python
def grep(
    pattern: str,
    path: str | Path = ".",
    *,
    glob: Optional[List[str]] = None,
    type: Optional[str] = None,          # 语言的短名，如 "py"
    ignore_case: bool = False,           # -i
    output_mode: OutputMode = "content", # content | files_with_matches | count | content_with_line_numbers
    context_before: int = 0,             # -B / -C 的 before 部分
    context_after: int = 0,              # -A / -C 的 after 部分
    multiline: bool = False,             # 跨行匹配（re.DOTALL + 按文件整体匹配）
    regex: bool = True,                  # True=正则；False=字面量（re.escape）
    show_filename: bool = True,          # 单文件时是否仍带文件名前缀
    max_results: int = 100,              # 单文件最大匹配数上限（防超大文件）
    head_limit: Optional[int] = None,    # 全局输出行数上限
    encoding: str = "utf-8",
) -> GrepResult
```

便捷封装（对齐 Claude Code 的 `-A/-B/-C`）：
```python
def grep_context(pattern, path, *, before=0, after=0, **kw) -> GrepResult
# 等价于 context_before=before, context_after=after
```

## 参数语义细节

- **`pattern`**：必填。`regex=False` 时用 `re.escape` 转字面量。
- **`path`**：文件或目录。默认 cwd（与 Claude Code 一致，但实现里要求调用方传绝对路径或显式 "."；保持与 read.py 一致的"必须绝对路径"会破坏 grep 的灵活性，所以这里**放宽**：允许相对路径，内部 resolve）。目录则递归遍历。
- **`glob`**：include 过滤，复用 `fnmatch`。多个 glob 取并集。支持 `**` 递归（用 `glob` 模块的 `recursive=True` 或自己处理）。
- **`type`**：语言短名→扩展名映射表（内置一份小型表，覆盖 py/js/ts/rs/go/c/java/md/json/yaml 等，对齐 ripgrep `--type`）。未知 type 报 `GrepError`。
- **`ignore_case`**：`re.IGNORECASE`。
- **`output_mode`**：
  - `files_with_matches`：只返回匹配的文件路径，不返回行内容。
  - `count`：每个文件的匹配行数（不返回行内容）。
  - `content`：返回匹配行（带 `file:line:content` 格式，类似 `grep -rn` 但用 `:` 分隔）。
  - `content_with_line_numbers`：显式带行号（content 模式其实已带，此模式用于无歧义场景；与 Claude Code 的 `-n` 对应）。
- **`context_before/after`**：上下文行。content/count/files_with_matches 模式下，上下文只在 content 类模式下有意义；非 content 模式给非零上下文 → 忽略并 warning（或直接报错？倾向于忽略，保持宽松）。
- **`multiline`**：True 时按整个文件内容做 `re.finditer`，match 的行号取起始行；否则逐行 `re.search`。
- **`max_results`**：单文件命中达到此数后停止读该文件（防巨型文件/日志爆炸）。
- **`head_limit`**：全局输出匹配数上限，超过则截断，`GrepResult.truncated=True`。

## 结果类型

```python
@dataclass
class GrepMatch:
    file_path: str
    line_number: int          # 1-based
    line: str                 # 匹配行原文（去尾换行）
    match_start: int = 0      # 行内匹配起始列（0-based）
    match_end: int = 0        # 行内匹配结束列
    before: List[str] = field(default_factory=list)  # 上下文 before
    after: List[str] = field(default_factory=list)   # 上下文 after

@dataclass
class GrepFileResult:
    file_path: str
    matches: List[GrepMatch]
    match_count: int          # 该文件匹配行数（count 模式用）

@dataclass
class GrepResult:
    pattern: str
    path: str
    files: List[GrepFileResult]
    total_matches: int
    files_with_matches: int
    truncated: bool
    output_mode: str
    content: str              # 格式化后的可读输出（__str__ 返回它）
    def __str__(self): return self.content
    def __bool__(self): return self.total_matches > 0
```

## 输出格式（content 模式）

仿 `grep -rn`，但统一用 `:` 分隔：

```
src/grep.py:42:    pattern: str,
src/grep.py:58:def grep(
```

多文件时每文件之间不加空行；带上下文时，上下文行用 `file:line-content`（`-` 分隔）形式，匹配行用 `file:line:content`（`:`），与 ripgrep 一致。上下文块之间空一行。

## 遍历与忽略规则

- 用 `os.walk` 遍历目录，`topdown=True` 以便用 `dirs[:]` 剪枝。
- 默认跳过：`.git`、`node_modules`、`.venv`、`__pycache__`、常见大二进制目录。
- 尊重 `.gitignore`：尝试 import `pathspec`（若可用）解析 `.gitignore`；不可用时降级为"仅跳过常见忽略目录"。codex 的 `require_git(true)` 语义（仅 repo 内才用 gitignore）在此实现为：仅当向上能找到 `.git` 时才读途中的 `.gitignore`。
- 二进制文件检测：读到 NUL 字节（`\x00`）即判定为二进制，跳过该文件（仿 ripgrep）。
- 符号链接目录：默认不跟随（防循环），与 `os.walk` 默认一致。

## 异常类型

```python
class GrepError(Exception): ...
class GrepPatternError(GrepError): ...      # 非法正则
class GrepPathNotFoundError(GrepError): ... # path 不存在
class GrepUnknownTypeError(GrepError): ...  # 未知 type 短名
```

## type→扩展名映射（内置，可扩展）

小型字典，覆盖常见语言。未覆盖的让用户用 `glob` 代替。

## 测试（内嵌 `__main__` + tests/test_grep.py）

内嵌自测覆盖：
- 基本正则匹配、字面量模式
- ignore_case
- glob 过滤（include / exclude）
- type 过滤
- 4 种 output_mode
- 上下文 before/after
- multiline 跨行匹配
- 二进制文件跳过
- 大文件 max_results 截断
- head_limit 全局截断
- 目录遍历跳过 .git/node_modules
- 路径不存在报错
- 非法正则报错
- 单文件 vs 多文件输出格式

同时写 `tests/test_grep.py`（pytest，与 test_edit.py 风格一致），用 `tmp_path` fixture。

## 不做的事

- 不调用外部 `rg`（保持纯 Python、无外部依赖）。
- 不实现 ripgrep 的完整 flag 集（如 `-w` word-regexp、`-v` invert、`--hidden` 显式含隐藏文件）——这些可后续按需加，本轮先对齐 Claude Code Grep 的核心参数。
- 不做 PCRE 高级特性（lookbehind 等靠 `re` 原生支持即可）。

## 依赖

无新增第三方依赖。仅标准库 `re`、`os`、`fnmatch`、`pathlib`、`dataclasses`、`typing`。`pathspec` 作为可选增强（try import）。

## 交付物

1. `src/grep.py`
2. `tests/test_grep.py`
3. 运行 `uv run pytest tests/test_grep.py` 与 `uv run python src/grep.py` 自测均通过。
