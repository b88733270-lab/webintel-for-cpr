"""Extraction/normalisation de numéros de téléphone : `parse_phones(text)`.

Détecte les formats internationaux (+226..., 0033...) et locaux
(06 12 34 56 78), normalise en supprimant espaces/points/tirets/parenthèses
("00" initial -> "+"), déduplique en conservant l'ordre d'apparition.
"""

from __future__ import annotations

import re

# Au moins ~7 chiffres au total (7 à 15 chiffres conservés ensuite).
_PHONE_RE = re.compile(r"(?:\+|00)?\d[\d\s()./\-]{5,}\d")
_STRIP_CHARS_RE = re.compile(r"[\s.()/\-]")


def _normalize(raw: str) -> str | None:
    normalized = _STRIP_CHARS_RE.sub("", raw)
    digits = re.sub(r"\D", "", normalized)  # ne garde que les chiffres
    if not 7 <= len(digits) <= 15:
        return None
    if normalized.startswith("00"):
        normalized = "+" + normalized[2:]
    return normalized


def parse_phones(text: str) -> list[str]:
    """Retourne la liste dédupliquée des téléphones trouvés dans `text`."""
    if not text:
        return []
    found: list[str] = []
    for match in _PHONE_RE.finditer(text):
        normalized = _normalize(match.group(0))
        if normalized and normalized not in found:
            found.append(normalized)
    return found
