"""Bounded byte buffers used by command-execution clients."""

from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
MAX_CONFIGURABLE_OUTPUT_BYTES = 16 * 1024 * 1024
OUTPUT_READ_CHUNK_BYTES = 64 * 1024
OUTPUT_DRAIN_TIMEOUT_SECS = 2.0
TERMINATION_GRACE_SECS = 1.0


def validate_max_output_bytes(value: int) -> int:
    """Validate the per-stream retained-output budget."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("max_output_bytes must be an integer")
    if value <= 0:
        raise ValueError("max_output_bytes must be greater than zero")
    if value > MAX_CONFIGURABLE_OUTPUT_BYTES:
        raise ValueError(
            "max_output_bytes must not exceed "
            f"{MAX_CONFIGURABLE_OUTPUT_BYTES} bytes"
        )
    return value


@dataclass
class HeadTailBytes:
    """Retain a stable prefix and rolling suffix while counting all bytes."""

    max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    _head: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _tail: bytearray = field(default_factory=bytearray, init=False, repr=False)
    total_bytes: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.max_bytes = validate_max_output_bytes(self.max_bytes)

    @property
    def head_budget(self) -> int:
        return self.max_bytes // 2

    @property
    def tail_budget(self) -> int:
        return self.max_bytes - self.head_budget

    @property
    def retained_bytes(self) -> int:
        return len(self._head) + len(self._tail)

    @property
    def omitted_bytes(self) -> int:
        return max(0, self.total_bytes - self.retained_bytes)

    @property
    def truncated(self) -> bool:
        return self.omitted_bytes > 0

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.total_bytes += len(chunk)

        head_remaining = self.head_budget - len(self._head)
        head_take = min(max(head_remaining, 0), len(chunk))
        if head_take:
            self._head.extend(chunk[:head_take])

        remainder = chunk[head_take:]
        if not remainder:
            return
        self._tail.extend(remainder)
        overflow = len(self._tail) - self.tail_budget
        if overflow > 0:
            del self._tail[:overflow]

    def raw_bytes(self) -> bytes:
        return bytes(self._head) + bytes(self._tail)

    def display_bytes(self) -> bytes:
        if not self.truncated:
            return self.raw_bytes()
        marker = (
            f"\n... [truncated, {self.omitted_bytes} bytes omitted] ...\n".encode()
        )
        return bytes(self._head) + marker + bytes(self._tail)

    def text(self) -> str:
        return self.display_bytes().decode("utf-8", errors="replace")

