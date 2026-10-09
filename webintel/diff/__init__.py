"""Diff intelligent : pondération des champs + moteur de comparaison."""

from webintel.diff.engine import DiffChange, DiffResult, build_dom_signature, diff_snapshots
from webintel.diff.scoring import FIELD_WEIGHTS, TEXT_SIMILARITY_MINOR, field_importance

__all__ = [
    "FIELD_WEIGHTS",
    "TEXT_SIMILARITY_MINOR",
    "DiffChange",
    "DiffResult",
    "build_dom_signature",
    "diff_snapshots",
    "field_importance",
]
