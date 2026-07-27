"""Structured multi-file patches for local and SSH filesystems."""

from __future__ import annotations

import ntpath
import os
import posixpath
import sys
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Optional

from .. import _core
from ..client import (
    Client,
    ClientError,
    resolve_client as _resolve_client,
    run_blocking,
)

BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
ADD_FILE = "*** Add File: "
DELETE_FILE = "*** Delete File: "
UPDATE_FILE = "*** Update File: "
MOVE_TO = "*** Move to: "
EOF_MARKER = "*** End of File"
CHANGE_CONTEXT = "@@ "
EMPTY_CHANGE_CONTEXT = "@@"


class PatchError(Exception):
    """Base error raised while parsing or applying a patch."""


class PatchParseError(PatchError):
    """Raised when a patch document does not follow the required grammar."""

    def __init__(self, message: str, line_number: int = 0):
        self.line_number = line_number
        loc = f" (第 {line_number} 行)" if line_number else ""
        super().__init__(f"{message}{loc}")


class PatchApplyError(PatchError):
    """Raised when patch path validation or filesystem mutation fails."""


class PatchSeekError(PatchError):
    """Raised when an update chunk cannot be located."""


@dataclass
class UpdateFileChunk:
    """One context-aware update within a file hunk."""

    change_context: Optional[str] = None
    old_lines: List[str] = field(default_factory=list)
    new_lines: List[str] = field(default_factory=list)
    is_end_of_file: bool = False


@dataclass
class Hunk:
    """One add, delete, or update operation."""

    type: str
    path: str
    contents: Optional[str] = None
    move_path: Optional[str] = None
    chunks: List[UpdateFileChunk] = field(default_factory=list)


@dataclass
class PatchResult:
    """Files affected by a completed patch."""

    added: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    patch: str = ""

    @property
    def affected_files(self) -> List[str]:
        return self.added + self.modified + self.deleted

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.modified or self.deleted)


class _PatchParser:
    """Parse a complete structured patch."""

    def __init__(self) -> None:
        self._line_number: int = 0
        self._state: str = "not_started"
        self._hunks: List[Hunk] = []
        self._current_chunk: Optional[UpdateFileChunk] = None
        self._add_file_parts: list[str] | None = None

    def parse(self, text: str) -> List[Hunk]:
        for line in text.splitlines():
            self._line_number += 1
            self._process_line(line)
        if self._state != "ended":
            raise PatchParseError("patch 最后一行必须是 '*** End Patch'")
        return deepcopy(self._hunks)

    def _flush_chunk(self) -> None:
        if self._state == "update_file" and self._current_chunk is not None:
            hunk = self._hunks[-1]
            if (
                self._current_chunk.old_lines
                or self._current_chunk.new_lines
                or self._current_chunk.change_context is not None
            ):
                hunk.chunks.append(self._current_chunk)
            self._current_chunk = None

    def _flush_add_file(self) -> None:
        if self._state == "add_file" and self._add_file_parts is not None:
            self._hunks[-1].contents = "".join(self._add_file_parts)
            self._add_file_parts = None

    def _ensure_update_not_empty(self) -> None:
        if self._state == "update_file":
            self._flush_chunk()
            hunk = self._hunks[-1]
            if not hunk.chunks and hunk.move_path is None:
                raise PatchParseError(
                    "Update File hunk 不能为空", self._line_number
                )

    def _try_header(self, line: str) -> bool:
        # Headers must start in column 1. Using line.strip() here makes valid
        # update context such as " *** End Patch" look like a control marker.
        header = line.rstrip()
        if header == END_PATCH:
            self._flush_add_file()
            self._ensure_update_not_empty()
            self._state = "ended"
            return True
        if header.startswith(ADD_FILE):
            self._flush_add_file()
            self._ensure_update_not_empty()
            path = header[len(ADD_FILE) :].strip()
            self._hunks.append(Hunk(type="add", path=path, contents=""))
            self._state = "add_file"
            self._add_file_parts = []
            return True
        if header.startswith(DELETE_FILE):
            self._flush_add_file()
            self._ensure_update_not_empty()
            path = header[len(DELETE_FILE) :].strip()
            self._hunks.append(Hunk(type="delete", path=path))
            self._state = "delete_file"
            return True
        if header.startswith(UPDATE_FILE):
            self._flush_add_file()
            self._ensure_update_not_empty()
            path = header[len(UPDATE_FILE) :].strip()
            self._hunks.append(Hunk(type="update", path=path))
            self._state = "update_file"
            self._current_chunk = UpdateFileChunk()
            return True
        if header.startswith(MOVE_TO):
            if self._state != "update_file":
                raise PatchParseError("*** Move to: 只能出现在 Update File 中", self._line_number)
            self._hunks[-1].move_path = header[len(MOVE_TO) :].strip()
            return True
        return False

    def _process_line(self, line: str) -> None:
        trimmed = line.strip()

        if self._state == "not_started":
            if trimmed == BEGIN_PATCH:
                self._state = "started"
                return
            raise PatchParseError("patch 第一行必须是 '*** Begin Patch'")

        if self._state == "started":
            if self._try_header(line):
                return
            raise PatchParseError(
                f"'{trimmed}' 不是有效的 hunk 头",
                self._line_number,
            )

        if self._state == "add_file":
            if self._try_header(line):
                return
            if line.startswith("+"):
                assert self._add_file_parts is not None
                self._add_file_parts.extend((line[1:], "\n"))
                return
            raise PatchParseError(
                f"Add File 内容行应以 '+' 开头: '{trimmed}'",
                self._line_number,
            )

        if self._state == "delete_file":
            if self._try_header(line):
                return
            raise PatchParseError(
                f"Delete File hunk 不应包含内容行: '{trimmed}'",
                self._line_number,
            )

        if self._state == "update_file":
            if self._try_header(line):
                return
            if line.rstrip() == EOF_MARKER:
                if self._current_chunk is None:
                    self._current_chunk = UpdateFileChunk()
                self._current_chunk.is_end_of_file = True
                return
            if line.rstrip() == EMPTY_CHANGE_CONTEXT or line.startswith(CHANGE_CONTEXT):
                if self._current_chunk is not None and (
                    self._current_chunk.old_lines
                    or self._current_chunk.new_lines
                    or self._current_chunk.change_context is not None
                ):
                    self._hunks[-1].chunks.append(self._current_chunk)
                ctx = None
                if line.startswith(CHANGE_CONTEXT):
                    ctx = line.rstrip()[len(CHANGE_CONTEXT) :]
                elif line.rstrip() == EMPTY_CHANGE_CONTEXT:
                    ctx = None
                self._current_chunk = UpdateFileChunk(change_context=ctx)
                return
            if self._current_chunk is None:
                self._current_chunk = UpdateFileChunk()
            if line.startswith(" "):
                content = line[1:]
                self._current_chunk.old_lines.append(content)
                self._current_chunk.new_lines.append(content)
                return
            if line.startswith("-"):
                self._current_chunk.old_lines.append(line[1:])
                return
            if line.startswith("+"):
                self._current_chunk.new_lines.append(line[1:])
                return
            raise PatchParseError(
                f"无效的 Update File 行: '{trimmed}'",
                self._line_number,
            )

        if self._state == "ended":
            raise PatchParseError("*** End Patch 之后还有内容", self._line_number)


@dataclass
class _FileState:
    exists: bool
    is_file: bool = False
    is_dir: bool = False
    content: str | None = None


def _path_identity(
    path: str,
    client_kind: str,
    *,
    os_name: str | None = None,
    platform_name: str | None = None,
) -> str:
    """Return a conservative identity key for same-file collision checks."""
    if client_kind != "local":
        return path
    target_os = os.name if os_name is None else os_name
    target_platform = sys.platform if platform_name is None else platform_name
    if target_os == "nt":
        return ntpath.normcase(ntpath.normpath(path))
    if target_platform == "darwin":
        # Default APFS/HFS+ volumes are case-insensitive and canonically
        # decomposing. Reject aliases conservatively; case-sensitive volumes
        # can still use separate operations instead of one ambiguous patch.
        return unicodedata.normalize("NFD", posixpath.normpath(path)).casefold()
    return path


async def apply_patch(
    patch_text: str,
    cwd: str | None = None,
    *,
    client: Client | None = None,
) -> PatchResult:
    """Apply a structured patch to one or more text files.

    All patch paths are resolved through the selected local or SSH client. The
    complete patch is parsed and preflighted against an in-memory filesystem
    view before writes begin. Syntax errors, missing sources, existing
    destinations, and unmatched update context therefore prevent earlier hunks
    from being applied. Low-level I/O failures during the final write/delete
    phase cannot be rolled back transactionally.

    This is not a unified diff: omit ``diff --git``, ``---``, ``+++``, and
    numeric hunk ranges. Every control marker (``***`` and ``@@``) must start
    in column 1. Inside ``Update File``, every content line needs a
    one-character prefix: space for unchanged context, ``-`` for removal, or
    ``+`` for addition. A blank content line still needs a prefix.
    The Markdown fences around the examples below are documentation delimiters
    only; pass only the lines between them as ``patch_text``.

    Replacement with unchanged context:

    ```text
    *** Begin Patch
    *** Update File: settings.txt
    @@
     [theme]
    -color=blue
    +color=green
    *** End Patch
    ```

    The character before ``[theme]`` above is the required space prefix.

    Pure insertion after a unique line:

    ```text
    *** Begin Patch
    *** Update File: app.py
    @@ def main():
    +    log_startup()
    *** End Patch
    ```

    EOF-anchored replacement:

    ```text
    *** Begin Patch
    *** Update File: app.py
    @@
    -raise SystemExit(main())
    +raise SystemExit(run())
    *** End of File
    *** End Patch
    ```

    Move without changing contents:

    ```text
    *** Begin Patch
    *** Update File: old_name.py
    *** Move to: new_name.py
    *** End Patch
    ```

    ``Add File`` requires a nonexistent target and ``+``-prefixed content.
    ``Delete File`` accepts no content and removes an existing regular file.
    ``Update File`` requires an existing regular file.

    ``@@`` starts a chunk and does not take diff line numbers.
    ``@@ <context>`` seeks to that literal full line and starts after it, so an
    add-only chunk inserts immediately after the named line. Without named
    context, an add-only chunk inserts at EOF. ``*** End of File`` after a
    chunk requires the old lines to match at EOF. Matching tries exact lines
    first, then whitespace and Unicode-normalized fallbacks. ``*** Move to:``
    is valid only inside an update and requires a nonexistent destination.

    Args:
        patch_text: Complete document including ``*** Begin Patch`` and
            ``*** End Patch``. A surrounding ``<<EOF`` heredoc is accepted.
        cwd: Optional local cwd used only when ``client`` is omitted.
        client: Existing local or SSH client. When omitted, create a local
            client rooted at ``cwd`` or the process working directory.

    Returns:
        A :class:`PatchResult` listing added, modified, and deleted
        patch-relative paths. Moves are reported as modified destination paths.

    Raises:
        PatchParseError: If the patch grammar is invalid or incomplete.
        PatchSeekError: If an update chunk cannot be located.
        PatchApplyError: If a path precondition fails or filesystem I/O fails.

    Warning:
        Delete and move hunks remove source files after destination writes
        complete. Review all patch paths before applying the document.
    """
    if client is not None:
        backend = client
    elif cwd:
        backend = _resolve_client(client="local", cwd=str(cwd))
    else:
        backend = _resolve_client()

    text = patch_text.removesuffix("\n").removesuffix("\r")
    if text.startswith("<<"):
        first_nl = text.find("\n")
        if first_nl != -1:
            header = text[:first_nl]
            tag = header[2:]
            if tag:
                heredoc_lines = text[first_nl + 1 :].split("\n")
                if heredoc_lines and heredoc_lines[-1] == tag:
                    text = "\n".join(heredoc_lines[:-1])

    hunks = await run_blocking(_PatchParser().parse, text)
    if not hunks:
        raise PatchApplyError("patch 不包含任何文件操作")

    result = PatchResult(patch=text)
    initial: dict[str, _FileState] = {}
    current: dict[str, _FileState] = {}
    path_aliases: dict[str, str] = {}

    def canonical_path(path: str) -> str:
        identity = _path_identity(path, backend.kind)
        return path_aliases.setdefault(identity, path)

    def validate_hunk_path(path: str, marker: str) -> None:
        if not path or "\x00" in path:
            raise PatchApplyError(f"{marker} 路径无效: {path!r}")

    async def load(path: str) -> _FileState:
        if path in current:
            return current[path]
        try:
            exists = await backend.exists(path)
            state = _FileState(exists=exists)
            if exists:
                state.is_dir = await backend.is_dir(path)
                state.is_file = await backend.is_file(path)
        except ClientError as e:
            raise PatchApplyError(f"无法检查路径 {path}: {e}") from e
        initial[path] = deepcopy(state)
        current[path] = state
        return state

    async def read_content(path: str, state: _FileState) -> str:
        if state.content is None:
            try:
                state.content = await backend.read_text(path)
            except ClientError as e:
                raise PatchApplyError(f"无法读取文件 {path}: {e}") from e
            if path in initial and initial[path].content is None:
                initial[path].content = state.content
        return state.content

    for hunk in hunks:
        validate_hunk_path(hunk.path, hunk.type)
        path = canonical_path(await backend.resolve(hunk.path))

        if hunk.type == "add":
            state = await load(path)
            if state.exists:
                raise PatchApplyError(f"Add File 目标已存在: {path}")
            content = hunk.contents or ""
            if content and not content.endswith("\n"):
                content += "\n"
            current[path] = _FileState(exists=True, is_file=True, content=content)
            result.added.append(hunk.path)

        elif hunk.type == "delete":
            state = await load(path)
            if not state.exists:
                raise PatchApplyError(f"要删除的文件不存在: {path}")
            if not state.is_file:
                raise PatchApplyError(f"路径不是普通文件: {path}")
            current[path] = _FileState(exists=False)
            result.deleted.append(hunk.path)

        elif hunk.type == "update":
            state = await load(path)
            if not state.exists:
                raise PatchApplyError(f"要修改的文件不存在: {path}")
            if not state.is_file:
                raise PatchApplyError(f"路径不是普通文件: {path}")
            original = await read_content(path, state)

            chunks = [
                (
                    c.change_context,
                    list(c.old_lines),
                    list(c.new_lines),
                    c.is_end_of_file,
                )
                for c in hunk.chunks
            ]
            try:
                new_content = await run_blocking(
                    _core.derive_new_contents,
                    original,
                    path,
                    chunks,
                )
            except ValueError as e:
                raise PatchSeekError(f"无法应用 patch 到 {path}: {e}") from e

            if hunk.move_path:
                validate_hunk_path(hunk.move_path, "move")
                write_path = canonical_path(await backend.resolve(hunk.move_path))
                if write_path == path:
                    raise PatchApplyError(f"Move 目标与源文件相同: {path}")
                destination = await load(write_path)
                if destination.exists:
                    raise PatchApplyError(f"Move 目标已存在: {write_path}")
                current[write_path] = _FileState(
                    exists=True, is_file=True, content=new_content
                )
                current[path] = _FileState(exists=False)
                result.modified.append(hunk.move_path)
            else:
                current[path] = _FileState(
                    exists=True, is_file=True, content=new_content
                )
                result.modified.append(hunk.path)

    for path, state in current.items():
        before = initial[path]
        if state.exists and state.is_file:
            if not before.exists or state.content != before.content:
                try:
                    await backend.write_text(path, state.content or "")
                except ClientError as e:
                    raise PatchApplyError(f"无法写入文件 {path}: {e}") from e

    for path, state in current.items():
        before = initial[path]
        if before.exists and before.is_file and not state.exists:
            try:
                await backend.delete(path)
            except ClientError as e:
                raise PatchApplyError(f"无法删除文件 {path}: {e}") from e

    return result


__all__ = ["apply_patch"]
