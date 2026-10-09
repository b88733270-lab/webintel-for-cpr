"""Poids des champs pour le diff intelligent (contrat §8).

`FIELD_WEIGHTS` : importance relative de chaque champ surveillé.
`field_importance(field)` : "high" (>= 70), "medium" (>= 25), "low" sinon.
`TEXT_SIMILARITY_MINOR` : seuil de similarité texte sous lequel un changement
de contenu est considéré significatif (MINOR).
"""

from __future__ import annotations

FIELD_WEIGHTS: dict[str, float] = {
    "price.amount": 100,
    "price.currency": 80,
    "availability": 90,
    "title": 40,
    "h1": 30,
    "meta_description": 20,
    "text": 25,
    "images": 10,
    "links": 5,
    "technologies": 15,
    "seo_score": 10,
}

# Similarité texte (difflib, 0..1) : en dessous -> MINOR, au-dessus -> COSMETIC.
TEXT_SIMILARITY_MINOR: float = 0.95


def field_importance(field: str) -> str:
    """Importance d'un champ : "high" si poids >= 70, "medium" si >= 25, sinon "low"."""
    weight = FIELD_WEIGHTS.get(field, 0)
    if weight >= 70:
        return "high"
    if weight >= 25:
        return "medium"
    return "low"
