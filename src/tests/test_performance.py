"""Lightweight regression checks for the hot Rust paths."""

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
