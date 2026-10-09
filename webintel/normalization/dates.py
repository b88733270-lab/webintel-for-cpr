"""Extraction/normalisation de dates : `parse_date(text) -> datetime | None`.

Gère : ISO 8601, "12/09/2026" et "12-09-2026" (jour d'abord, convention FR),
"12 sept. 2026" / "12 septembre 2026" (mois en français), timestamps unix
(10 chiffres -> UTC). Retourne None si rien n'est reconnu.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

_MONTHS_FR = {
    "janvier": 1, "janv": 1, "jan": 1,
    "fevrier": 2, "février": 2, "fevr": 2, "févr": 2, "fev": 2, "fév": 2,
    "mars": 3, "mar": 3,
    "avril": 4, "avr": 4,
    "mai": 5,
    "juin": 6, "jun": 6,
    "juillet": 7, "juil": 7, "jul": 7,
    "aout": 8, "août": 8,
    "septembre": 9, "sept": 9, "sep": 9,
    "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11,
    "decembre": 12, "décembre": 12, "dec": 12, "déc": 12,
}

_NUMERIC_RE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$")
_FRENCH_RE = re.compile(r"^(\d{1,2})\s+([a-zàâäéèêëîïôöùûüç]+)\.?(?:\s+(\d{4}))?$")
_TIMESTAMP_RE = re.compile(r"^\d{10}$")


def _normalize_year(year: int) -> int:
    if year < 100:  # année sur 2 chiffres -> siècle courant / précédent (POSIX)
        return 2000 + year if year <= 68 else 1900 + year
    return year


def _safe_date(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def parse_date(text: str) -> datetime | None:
    """Parse une date FR/ISO/timestamp ; None si non reconnue."""
    if not text:
        return None
    t = text.strip()
    if not t:
        return None

    # 1) Timestamp unix (exactement 10 chiffres) -> UTC.
    if _TIMESTAMP_RE.fullmatch(t):
        try:
            return datetime.fromtimestamp(int(t), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    # 2) ISO 8601 ("2026-09-12", "2026-09-12T14:30:00Z"...).
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        pass

    # 3) Numérique FR "12/09/2026", "12-09-2026" (jour/mois/année).
    m = _NUMERIC_RE.fullmatch(t)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), _normalize_year(int(m.group(3)))
        return _safe_date(year, month, day)

    # 4) Français littéral "12 sept. 2026" / "12 septembre 2026".
    m = _FRENCH_RE.fullmatch(t.lower())
    if m:
        day = int(m.group(1))
        month = _MONTHS_FR.get(m.group(2).rstrip("."))
        if month is None:
            return None
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        return _safe_date(year, month, day)

    return None
