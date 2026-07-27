"""Deterministic text replacement, file creation, and prepending."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .. import _core
from ..client import (
    Client,
    ClientError,
    resolve_client as _resolve_client,
    run_blocking,
)


class EditError(Exception):
    """Base error raised while editing a file."""


class EditFileNotFoundError(EditError):
    """Raised when an operation requires an existing file."""


class EditFileExistsError(EditError):
    """Raised when file creation targets an existing path."""


class EditStringNotFoundError(EditError):
    """Raised when ``old_string`` cannot be matched."""


class EditAmbiguousMatchError(EditError):
    """Raised when a replacement is ambiguous."""


@dataclass
class EditResult:
    """Resolved path and edit operation summary."""

    file_path: str
    replacements: int
    is_new_file: bool = False
    operation: Literal["replaced", "created", "prepended"] = "replaced"

    def __bool__(self) -> bool:
        return self.replacements > 0 or self.operation in {"created", "prepended"}


async def edit(
    file_path: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
    prepend: bool = False,
    encoding: str = "utf-8",
    client: Client | None = None,
) -> EditResult:
    """Replace a specific string in a local or remote UTF-8 text file.

    Matching first searches for exact substring occurrences. If none exist, it
    retries with trailing whitespace ignored independently on each line. By
    default ``old_string`` must match exactly once: no match and ambiguous
    multiple matches raise explicit errors instead of modifying the wrong
    location. Set ``replace_all`` only when every non-overlapping occurrence is
    intended to change.

    An empty ``old_string`` means create a new file, including its parent
    directories. If the target already exists, creation fails instead of
    overwriting or silently prepending. Prepending is a separate explicit
    operation: set ``prepend=True`` together with an empty ``old_string``; the
    target must already exist.
    ``append`` is not a mode of this tool. To append lines, use
    :func:`apply_patch` with an ``Update File`` hunk containing a bare ``@@``
    and only ``+`` lines; an add-only chunk without named context inserts at
    EOF.

    Like :func:`write`, this tool never adds a trailing newline automatically.
    A normal replacement otherwise preserves the file's existing end-of-file
    newline state unless the matched or replacement text explicitly changes it.

    Args:
        file_path: Absolute, home-relative, or client-cwd-relative file path.
        old_string: Literal text to replace. Preserve indentation and line
            breaks. An empty string means create a new file.
        new_string: Literal replacement text. When ``old_string`` is empty,
            this is the complete new-file content or, with ``prepend=True``,
            the content to prepend. Otherwise, an empty string deletes the
            matched text.
        replace_all: Replace all matches instead of requiring uniqueness.
        prepend: Explicitly prepend ``new_string`` to an existing file.
            Requires an empty ``old_string``. Defaults to false.
        encoding: Text encoding used for reading and writing. Defaults to UTF-8.
        client: Existing local or SSH client. When omitted, create a local
            client rooted at the process working directory.

    Returns:
        An :class:`EditResult` with the resolved path, operation
        (``replaced``, ``created``, or ``prepended``), replacement count, and
        whether a new file was created.

    Raises:
        ValueError: If ``prepend=True`` is combined with a non-empty
            ``old_string`` or with ``replace_all=True``.
        EditFileNotFoundError: If a non-empty match targets a missing file, or
            prepend targets a missing file.
        EditFileExistsError: If create-on-empty targets an existing file.
        EditStringNotFoundError: If ``old_string`` does not match.
        EditAmbiguousMatchError: If multiple matches exist and
            ``replace_all`` is false.
        EditError: If the path is not a regular file or I/O fails.
    """
    c = _resolve_client(client)
    path = await c.resolve(file_path)

    if prepend and old_string != "":
        raise ValueError("prepend=True 要求 old_string 为空字符串")
    if prepend and replace_all:
        raise ValueError("prepend=True 与 replace_all=True 不能同时使用")

    if old_string == "":
        if await c.exists(path):
            if not prepend:
                raise EditFileExistsError(
                    f"文件已存在，old_string 为空时只允许创建新文件: {path}\n"
                    "提示: 如需在现有文件开头插入内容，请显式设置 prepend=True。"
                )
            if await c.is_dir(path):
                raise EditError(f"路径是目录: {path}")
            if not await c.is_file(path):
                raise EditError(f"路径不是普通文件: {path}")
            try:
                content = await c.read_text(path, encoding=encoding)
            except ClientError as e:
                raise EditError(str(e)) from e
            line_ending = "\r\n" if "\r\n" in content else "\n"
            prefix = new_string
            if line_ending == "\r\n":
                prefix = prefix.replace("\r\n", "\n").replace("\n", "\r\n")
            new_content = prefix + content
            try:
                await c.write_text(path, new_content, encoding=encoding)
            except ClientError as e:
                raise EditError(str(e)) from e
            return EditResult(
                file_path=path,
                replacements=0,
                is_new_file=False,
                operation="prepended",
            )
        if prepend:
            raise EditFileNotFoundError(f"prepend=True 需要已存在的文件: {path}")
        try:
            await c.write_text(path, new_string, encoding=encoding)
        except ClientError as e:
            raise EditError(str(e)) from e
        return EditResult(
            file_path=path,
            replacements=0,
            is_new_file=True,
            operation="created",
        )

    if not await c.exists(path):
        raise EditFileNotFoundError(
            f"文件不存在: {path}\n"
            f"提示: 如果要在新文件中写入内容，请将 old_string 设为空字符串。"
        )
    if not await c.is_file(path):
        raise EditError(f"路径不是普通文件: {path}")

    try:
        content = await c.read_text(path, encoding=encoding)
    except ClientError as e:
        raise EditError(str(e)) from e

    try:
        new_content, count = await run_blocking(
            _core.edit_text,
            content,
            old_string,
            new_string,
            replace_all,
        )
    except ValueError as e:
        msg = str(e)
        if msg.startswith("NOT_FOUND:"):
            raise EditStringNotFoundError(
                f"在文件中未找到 old_string:\n"
                f"文件: {path}\n"
                f"--- old_string ---\n{old_string!r}\n--- 文件内容预览 ---\n"
                f"{content[:500]}{'...' if len(content) > 500 else ''}"
            ) from e
        if msg.startswith("AMBIGUOUS:"):
            matches = await run_blocking(_core.find_matches, content, old_string)
            raw = content.encode("utf-8")
            context_lines = []
            for i, (pos, mlen) in enumerate(matches[:5]):
                prefix = raw[:pos].decode("utf-8", errors="replace")
                line_num = prefix.count("\n") + 1
                start_ctx = max(0, pos - 60)
                end_ctx = min(len(raw), pos + mlen + 60)
                snippet = raw[start_ctx:end_ctx].decode("utf-8", errors="replace")
                context_lines.append(
                    f"  匹配 #{i + 1} (第 {line_num} 行): ...{snippet!r}..."
                )
            extra = "\n".join(context_lines)
            if len(matches) > 5:
                extra += f"\n  ... 及其他 {len(matches) - 5} 处匹配"
            raise EditAmbiguousMatchError(
                f"old_string 在文件中出现了 {len(matches)} 次（需要恰好 1 次）:\n"
                f"文件: {path}\n{extra}\n"
                f"提示: 请在 old_string 中包含更多上下文使其唯一，"
                f"或设置 replace_all=True。"
            ) from e
        raise EditError(msg) from e

    try:
        await c.write_text(path, new_content, encoding=encoding)
    except ClientError as e:
        raise EditError(str(e)) from e

    return EditResult(
        file_path=path,
        replacements=count,
        is_new_file=False,
        operation="replaced",
    )


__all__ = ["edit"]
