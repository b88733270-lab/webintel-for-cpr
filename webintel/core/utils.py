"""Utilitaires transverses : temps UTC, hachage, nettoyage de texte, similarité."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

_WHITESPACE_RE = re.compile(r"\s+")


def utcnow() -> datetime:
    """Horodatage UTC conscient du fuseau (à stocker en UTC partout)."""
    return datetime.now(timezone.utc)


def sha256_hex(s: str) -> str:
    """Empreinte SHA-256 hexadécimale d'une chaîne (UTF-8)."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def clean_text(s: str) -> str:
    """Normalise les espaces (multiples -> un seul) et supprime les bords."""
    if not s:
        return ""
    return _WHITESPACE_RE.sub(" ", s).strip()


def truncate(s: str, n: int) -> str:
    """Tronque à `n` caractères en ajoutant '…' si nécessaire."""
    if n < 0:
        raise ValueError("n doit être positif ou nul")
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def text_similarity(a: str, b: str) -> float:
    """Similarité texte 0..1 via `difflib.SequenceMatcher` (1 = identiques)."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()
