import os
import sys
from pathlib import Path

import pytest

from file_tools import ClientError, LocalClient
from file_tools.tools.apply_patch import (
    PatchApplyError,
    PatchParseError,
    apply_patch,
)

pytestmark = pytest.mark.anyio


async def test_bare_empty_update_line_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "example.txt").write_text("content\n")
    with pytest.raises(PatchParseError, match="Invalid Update File line"):
        await apply_patch(
            "*** Begin Patch\n"
            "*** Update File: example.txt\n"
            "@@\n"
            "\n"
            "*** End Patch\n",
            client=LocalClient(cwd=tmp_path),
        )


async def test_equal_position_insertions_preserve_patch_order(tmp_path: Path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("base\n")
    client = LocalClient(cwd=tmp_path)
    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: example.txt\n"
        "@@\n"
        "+one\n"
        "@@\n"
        "+two\n"
        "*** End Patch\n"
    )

    await apply_patch(patch_text, client=client)

    assert target.read_text() == "base\none\ntwo\n"


async def test_update_basic(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("foo\nbar\nbaz\n")
    client = LocalClient(cwd=tmp_path)
    await apply_patch(
        "*** Begin Patch\n"
        "*** Update File: f.txt\n"
        "@@\n"
        " foo\n"
        "-bar\n"
        "+BAR\n"
        " baz\n"
        "*** End Patch\n",
        client=client,
    )
    assert (tmp_path / "f.txt").read_text() == "foo\nBAR\nbaz\n"


async def test_named_context_add_only_chunk_inserts_after_context(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("def main():\n    return 0\n")

    await apply_patch(
        "*** Begin Patch\n"
        "*** Update File: app.py\n"
        "@@ def main():\n"
        "+    log_startup()\n"
        "*** End Patch\n",
        client=LocalClient(cwd=tmp_path),
    )

    assert target.read_text() == "def main():\n    log_startup()\n    return 0\n"


async def test_end_of_file_marker_anchors_replacement(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("raise SystemExit(main())\nraise SystemExit(main())\n")

    await apply_patch(
        "*** Begin Patch\n"
        "*** Update File: app.py\n"
        "@@\n"
        "-raise SystemExit(main())\n"
        "+raise SystemExit(run())\n"
        "*** End of File\n"
        "*** End Patch\n",
        client=LocalClient(cwd=tmp_path),
    )

    assert target.read_text() == (
        "raise SystemExit(main())\nraise SystemExit(run())\n"
    )


async def test_add_and_delete(tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("x\n")
    client = LocalClient(cwd=tmp_path)
    result = await apply_patch(
        "*** Begin Patch\n"
        "*** Add File: new.txt\n"
        "+hello\n"
        "*** Delete File: old.txt\n"
        "*** End Patch\n",
        client=client,
    )
    assert (tmp_path / "new.txt").read_text() == "hello\n"
    assert not (tmp_path / "old.txt").exists()
    assert result.added == ["new.txt"]
    assert result.deleted == ["old.txt"]


async def test_add_rejects_existing_file_without_overwriting(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("original\n")

    with pytest.raises(PatchApplyError, match="destination already exists"):
        await apply_patch(
            "*** Begin Patch\n"
            "*** Add File: existing.txt\n"
            "+replacement\n"
            "*** End Patch\n",
            client=LocalClient(cwd=tmp_path),
        )

    assert target.read_text() == "original\n"


async def test_same_path_move_is_rejected_without_deleting_source(tmp_path: Path) -> None:
    target = tmp_path / "same.txt"
    target.write_text("original\n")

    with pytest.raises(PatchApplyError, match="same as the source file"):
        await apply_patch(
            "*** Begin Patch\n"
            "*** Update File: same.txt\n"
            "*** Move to: same.txt\n"
            "*** End Patch\n",
            client=LocalClient(cwd=tmp_path),
        )

    assert target.read_text() == "original\n"


async def test_preflight_failure_does_not_apply_earlier_hunks(tmp_path: Path) -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Add File: created.txt\n"
        "+content\n"
        "*** Update File: missing.txt\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch\n"
    )

    with pytest.raises(PatchApplyError, match="does not exist"):
        await apply_patch(patch, client=LocalClient(cwd=tmp_path))

    assert not (tmp_path / "created.txt").exists()


async def test_commit_failure_rolls_back_earlier_writes(tmp_path: Path) -> None:
    delegate = LocalClient(cwd=tmp_path)

    class FailingClient:
        kind = "local"

        def __init__(self) -> None:
            self.failed = False

        def __getattr__(self, name: str):
            return getattr(delegate, name)

        async def write_text_atomic(self, path: str, content: str, **kwargs):
            if path.endswith("b.txt") and not self.failed:
                self.failed = True
                raise ClientError("injected write failure")
            return await delegate.write_text_atomic(path, content, **kwargs)

    patch = (
        "*** Begin Patch\n"
        "*** Add File: a.txt\n"
        "+a\n"
        "*** Add File: b.txt\n"
        "+b\n"
        "*** End Patch\n"
    )

    with pytest.raises(PatchApplyError, match="Patch commit failed"):
        await apply_patch(patch, client=FailingClient())  # type: ignore[arg-type]

    assert not (tmp_path / "a.txt").exists()
    assert not (tmp_path / "b.txt").exists()


async def test_move_rejects_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source\n")
    destination.write_text("destination\n")

    with pytest.raises(PatchApplyError, match="Move destination already exists"):
        await apply_patch(
            "*** Begin Patch\n"
            "*** Update File: source.txt\n"
            "*** Move to: destination.txt\n"
            "*** End Patch\n",
            client=LocalClient(cwd=tmp_path),
        )

    assert source.read_text() == "source\n"
    assert destination.read_text() == "destination\n"


async def test_update_preserves_crlf(tmp_path: Path) -> None:
    target = tmp_path / "windows.txt"
    target.write_bytes(b"foo\r\nbar\r\nbaz\r\n")

    await apply_patch(
        "*** Begin Patch\n"
        "*** Update File: windows.txt\n"
        "@@\n"
        "-bar\n"
        "+BAR\n"
        "*** End Patch\n",
        client=LocalClient(cwd=tmp_path),
    )

    assert target.read_bytes() == b"foo\r\nBAR\r\nbaz\r\n"


async def test_move_only_preserves_missing_final_newline(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_bytes(b"no-final-newline")

    await apply_patch(
        "*** Begin Patch\n"
        "*** Update File: source.txt\n"
        "*** Move to: moved.txt\n"
        "*** End Patch\n",
        client=LocalClient(cwd=tmp_path),
    )

    assert not (tmp_path / "source.txt").exists()
    assert (tmp_path / "moved.txt").read_bytes() == b"no-final-newline"


@pytest.mark.skipif(
    os.name != "nt" and sys.platform != "darwin",
    reason="local Linux filesystems are normally case-sensitive",
)
async def test_patch_rejects_case_alias_collisions_on_local_platform(
    tmp_path: Path,
) -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Add File: Case.txt\n"
        "+first\n"
        "*** Add File: case.txt\n"
        "+second\n"
        "*** End Patch\n"
    )

    with pytest.raises(PatchApplyError, match="destination already exists"):
        await apply_patch(patch, client=LocalClient(cwd=tmp_path))

    assert not (tmp_path / "Case.txt").exists()
    assert not (tmp_path / "case.txt").exists()
