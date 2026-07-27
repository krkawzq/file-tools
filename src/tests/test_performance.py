"""Lightweight regression checks for the hot Rust paths."""

from pathlib import Path
from time import perf_counter

import pytest

from file_tools import _core

MAX_SECONDS = 3.0


@pytest.mark.performance
def test_many_line_replacements_remain_linear() -> None:
    lines = [f"line-{i}" for i in range(50_000)]
    replacements = [(i, 1, [f"changed-{i}"]) for i in range(0, 50_000, 2)]

    started = perf_counter()
    result = _core.apply_line_replacements(lines, replacements)
    elapsed = perf_counter() - started

    assert len(result) == 50_000
    assert result[:3] == ["changed-0", "line-1", "changed-2"]
    assert elapsed < MAX_SECONDS, f"line replacement took {elapsed:.3f}s"


@pytest.mark.performance
def test_large_unicode_replace_all_stays_fast() -> None:
    text = "é-value\n" * 100_000

    started = perf_counter()
    result, count = _core.edit_text(text, "é", "x", True)
    elapsed = perf_counter() - started

    assert count == 100_000
    assert result.startswith("x-value\n")
    assert elapsed < MAX_SECONDS, f"unicode replacement took {elapsed:.3f}s"


@pytest.mark.performance
def test_large_file_window_does_not_materialize_the_full_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "large.txt"
    target.write_text("".join(f"line-{index}\n" for index in range(200_000)))
    client = _core.LocalClient(cwd=tmp_path, max_transfer_bytes=1024)

    started = perf_counter()
    text, total, start, end, truncated = client.read_text_window(
        "large.txt", 100_000, 3
    )
    elapsed = perf_counter() - started

    assert text == "line-99999\nline-100000\nline-100001\n"
    assert (total, start, end, truncated) == (200_000, 100_000, 100_002, True)
    assert elapsed < MAX_SECONDS, f"bounded file window took {elapsed:.3f}s"
