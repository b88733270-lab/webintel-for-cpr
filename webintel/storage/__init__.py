"""Stockage : snapshots de pages (append-only)."""

from webintel.storage.snapshots import get_history, get_latest, save_snapshot

__all__ = ["get_history", "get_latest", "save_snapshot"]
