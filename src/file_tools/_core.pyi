"""Type stubs for the Rust extension module ``file_tools._core``.

High-speed string operators (no I/O). Built via maturin / pyo3.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------

def find_matches(text: str, pattern: str) -> list[tuple[int, int]]:
    """Find all matches as ``(byte_start, byte_len)`` pairs.

    Strategy: exact substring first, then per-line rstrip match.
    """
    ...

def apply_replacements_text(
    text: str,
    matches: list[tuple[int, int]],
    new_string: str,
) -> str:
    """Apply non-overlapping replacements at the given byte spans."""
    ...

def edit_text(
    text: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[str, int]:
    """Match + replace. Returns ``(new_text, replacement_count)``.

    Does not add or remove a trailing newline beyond the literal replacement.
    Raises ``ValueError`` with ``NOT_FOUND:`` / ``AMBIGUOUS:`` prefixes.
    """
    ...

# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

def count_lines(text: str) -> int:
    """Count lines (non-empty file without trailing newline still counts last line)."""
    ...

def slice_lines(text: str, start: int, take: int) -> list[str]:
    """Slice lines by 0-based index; each line keeps its original ending."""
    ...

def format_cat_n(
    lines: list[str],
    start_line: int,
    total_lines: int,
    truncated: bool = False,
) -> str:
    """Format lines as ``cat -n`` (1-based numbers)."""
    ...

def prepare_read(
    text: str,
    offset: int,
    limit: int,
    show_line_numbers: bool = True,
) -> tuple[str, int, int, int, bool, list[str]]:
    """Agent read pipeline.

    Parameters
    ----------
    offset:
        1-based start line. Negative value ``-N`` means last N lines (tail).
    limit:
        Max lines to return (window size ignored when ``offset < 0``).

    Returns
    -------
    (content, total_lines, start_line, end_line, truncated, raw_lines)
    """
    ...

# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------

def seek_sequence(
    lines: list[str],
    pattern: list[str],
    start: int = 0,
    eof: bool = False,
) -> int | None:
    """Four-tier fuzzy line-sequence search (codex seek_sequence)."""
    ...

def apply_line_replacements(
    lines: list[str],
    replacements: list[tuple[int, int, list[str]]],
) -> list[str]:
    """Apply ``(start_idx, old_len, new_lines)`` replacements.

    Raises ``ValueError`` for out-of-range or overlapping spans.
    """
    ...

def derive_new_contents(
    original_content: str,
    path: str,
    chunks: list[tuple[str | None, list[str], list[str], bool]],
) -> str:
    """Compute new file text from update chunks.

    Each chunk is ``(change_context, old_lines, new_lines, is_end_of_file)``.
    """
    ...
