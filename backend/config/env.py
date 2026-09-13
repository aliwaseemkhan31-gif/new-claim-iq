"""Environment variable helpers.

A small, dependency-free reader. Deliberately not `django-environ`: an
air-gapped deployment benefits from every dependency it does not have, and this
is about sixty lines.

The important behaviour is :func:`env_str` with ``required=True``, which fails
at import time when a mandatory secret is absent. A missing ``SECRET_KEY``
should stop the process immediately, not surface later as a confusing
authentication failure.
"""
from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f"}


class ImproperlyConfigured(Exception):
    """A required environment variable is missing or malformed."""


def env_str(name: str, *, default: str | None = None, required: bool = False) -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        if required:
            raise ImproperlyConfigured(
                f"Environment variable {name!r} is required but was not set. "
                f"See .env.example."
            )
        return default if default is not None else ""
    return raw


def env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ImproperlyConfigured(
        f"Environment variable {name!r} must be a boolean, got {raw!r}."
    )


def env_int(name: str, *, default: int | None = None, required: bool = False) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        if required or default is None:
            raise ImproperlyConfigured(f"Environment variable {name!r} is required.")
        return default
    try:
        return int(raw)
    except ValueError:
        raise ImproperlyConfigured(
            f"Environment variable {name!r} must be an integer, got {raw!r}."
        ) from None


def env_list(name: str, *, default: list[str] | None = None) -> list[str]:
    """Read a comma-separated list. Blank entries are dropped."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return list(default) if default is not None else []
    return [item.strip() for item in raw.split(",") if item.strip()]
