"""Schémas Pydantic v2 des entités (contrat §3 d'architecture.md).

Chaque entité porte ses champs métier + `provenance: Provenance`,
`source_url: str` et `captured_at: datetime`. Les extracteurs de la vague 2
retournent des dicts bruts ; `coerce_entity()` les valide et les nettoie
(clés `_provenance` ignorées, prix normalisé, disponibilité bornée).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from webintel.core.types import Price, Provenance
from webintel.core.utils import utcnow

Availability = Literal["in_stock", "out_of_stock", "preorder", "unknown"]
_AVAILABILITY_VALUES = ("in_stock", "out_of_stock", "preorder", "unknown")


class BaseEntity(BaseModel):
    """Socle commun : provenance + URL source + horodatage de capture."""

    model_config = ConfigDict(extra="ignore")  # tolère "_provenance" et clés inconnues

    provenance: Provenance
    source_url: str = ""
    captured_at: datetime = Field(default_factory=utcnow)


class Product(BaseEntity):
    name: str | None = None
    brand: str | None = None
    model: str | None = None
    description: str | None = None
    sku: str | None = None
    ean: str | None = None
    category: str | None = None
    price: Price = Field(default_factory=lambda: Price(amount=None, currency=None))
    availability: Availability = "unknown"
    rating: float | None = None
    review_count: int | None = None
    images: list[str] = Field(default_factory=list)
    specifications: dict = Field(default_factory=dict)
    seller: dict = Field(default_factory=dict)

    @field_validator("availability", mode="before")
    @classmethod
    def _coerce_availability(cls, value):
        # Valeur inconnue -> "unknown" plutôt qu'une erreur de validation.
        if value in _AVAILABILITY_VALUES:
            return value
        return "unknown"


class Article(BaseEntity):
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    category: str | None = None
    content: str | None = None
    images: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class Company(BaseEntity):
    name: str | None = None
    website: str | None = None
    description: str | None = None
    industry: str | None = None
    location: str | None = None
    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    # [{network, url}] — même format que `extract_contacts` (et le template UI).
    socials: list[dict] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class WebPage(BaseEntity):
    title: str | None = None
    description: str | None = None
    headings: list = Field(default_factory=list)
    word_count: int = 0


ENTITY_SCHEMAS: dict[str, type[BaseEntity]] = {
    "product": Product,
    "article": Article,
    "company": Company,
    "webpage": WebPage,
}


def _default_provenance(source_url: str, raw_prov: dict | None) -> dict:
    """Construit une `Provenance` : reprend celle du champ nom/titre si
    présente (meilleure confiance), sinon une provenance générique."""
    if isinstance(raw_prov, dict):
        for key in ("name", "title"):
            entry = raw_prov.get(key)
            if isinstance(entry, dict) and entry.get("source"):
                return {
                    "source": str(entry.get("source", "extraction")),
                    "url": source_url,
                    "selector": entry.get("selector"),
                    "method": str(entry.get("method", "mixed")),
                    "confidence": float(entry.get("confidence", 0.5) or 0.5),
                }
    return {
        "source": "extraction",
        "url": source_url,
        "selector": None,
        "method": "mixed",
        "confidence": 0.5,
    }


def coerce_entity(entity_type: str, data: dict) -> BaseEntity:
    """Valide et nettoie un dict d'extracteur vers le schéma Pydantic du type.

    - type inconnu -> repli sur `WebPage` ;
    - `_provenance` (carte par champ des extracteurs) est convertie en une
      `Provenance` unique plutôt que conservée telle quelle ;
    - `price` dict -> `Price` ; `availability` invalide -> "unknown" ;
    - dates ISO -> `datetime`.
    """
    schema = ENTITY_SCHEMAS.get((entity_type or "").lower(), WebPage)
    raw = dict(data or {})
    raw_prov = raw.pop("_provenance", None)
    source_url = str(raw.get("source_url") or "")
    raw["provenance"] = _default_provenance(source_url, raw_prov if isinstance(raw_prov, dict) else None)
    raw["source_url"] = source_url
    return schema.model_validate(raw)
