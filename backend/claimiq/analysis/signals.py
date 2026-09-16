"""Signals other apps react to."""
from __future__ import annotations

from django.dispatch import Signal

#: Sent when an analysis run reaches a terminal status. Argument: ``analysis``.
analysis_finished = Signal()
