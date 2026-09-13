#!/usr/bin/env python
"""Django management entry point."""
from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django is not importable. Activate the virtual environment or run "
            "inside the backend container."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
