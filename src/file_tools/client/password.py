"""Password acquisition for SSH.

Inspired by sshpass ideas (explicit password, clear incorrect-password
semantics, no secret leakage in logs) — but we only support:

- explicit ``password`` string
- optional interactive ``getpass`` when a TTY is available

No env-var / file / fd sources.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum


class PasswordError(ValueError):
    """Invalid password prompt / empty interactive input."""


class IncorrectPasswordError(Exception):
    """Authentication failed with the supplied password."""


class PasswordSource(str, Enum):
    NONE = "none"
    EXPLICIT = "explicit"
    PROMPT = "prompt"


@dataclass(frozen=True)
class PasswordMaterial:
    """Resolved password plus provenance (never log ``value``)."""

    value: str
    source: PasswordSource

    def __repr__(self) -> str:  # pragma: no cover
        return f"PasswordMaterial(source={self.source!r}, value=***)"

    def __str__(self) -> str:  # pragma: no cover
        return f"PasswordMaterial(source={self.source!r}, value=***)"


def read_password_interactive(prompt_label: str = "SSH password") -> str:
    """Interactive password when stdin is a TTY."""
    if sys.stdin is None or not sys.stdin.isatty():
        raise PasswordError(
            "no TTY available for interactive password prompt "
            "(pass ssh_password or use ssh_key)"
        )
    import getpass

    try:
        entered = getpass.getpass(f"{prompt_label}: ")
    except (EOFError, KeyboardInterrupt) as e:
        raise PasswordError("password prompt cancelled") from e
    if entered is None or entered == "":
        raise PasswordError("empty password entered")
    return entered


@dataclass
class PasswordRequest:
    """How to obtain a password.

    - If ``password`` is non-empty → use it.
    - Else if ``allow_prompt`` and (retry or TTY) → interactive getpass.
    - Else → ``None`` (caller may try key-only auth).
    """

    password: str = ""
    allow_prompt: bool = True
    prompt_label: str = "SSH password"

    def resolve(self, *, for_retry: bool = False) -> PasswordMaterial | None:
        if self.password:
            return PasswordMaterial(self.password, PasswordSource.EXPLICIT)

        if not self.allow_prompt:
            return None

        if not for_retry and (sys.stdin is None or not sys.stdin.isatty()):
            return None

        return PasswordMaterial(
            read_password_interactive(self.prompt_label),
            PasswordSource.PROMPT,
        )


def resolve_password(
    *,
    password: str = "",
    allow_prompt: bool = True,
    prompt_label: str = "SSH password",
    for_retry: bool = False,
) -> str | None:
    """Convenience wrapper returning the password string or ``None``."""
    mat = PasswordRequest(
        password=password,
        allow_prompt=allow_prompt,
        prompt_label=prompt_label,
    ).resolve(for_retry=for_retry)
    return None if mat is None else mat.value
