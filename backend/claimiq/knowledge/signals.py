"""Signals other apps react to. Notifications subscribe; knowledge does not know them."""
from __future__ import annotations

from django.dispatch import Signal

#: Sent after a knowledge base changes status. Arguments: ``knowledge_base``,
#: ``previous_status``, ``actor`` (a user, or None for a background build).
knowledge_base_status_changed = Signal()
