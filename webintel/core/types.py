"""Contrats de données Pydantic v2 (contrat §3 d'architecture.md)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from webintel.core.utils import utcnow


class Provenance(BaseModel):
    """Origine d'une donnée extraite (traçabilité de chaque valeur)."""

    source: str  # ex. "json-ld", "heuristique-dom", "opengraph"
    url: str
    selector: str | None = None
    method: str  # ex. "css", "xpath", "regex", "jsonpath"
    captured_at: datetime = Field(default_factory=utcnow)
    confidence: float = 1.0  # 0..1


class Price(BaseModel):
    """Prix normalisé : montant + code devise ISO (EUR, USD, XOF...)."""

    amount: float | None
    currency: str | None


class FetchResult(BaseModel):
    """Résultat d'une récupération HTTP."""

    url: str
    final_url: str
    status_code: int
    headers: dict = Field(default_factory=dict)
    html: str = ""
    text: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)
    duration_ms: int = 0
    method: str = "http"  # "http" | "browser"
    needs_js: bool = False  # True si la page semble nécessiter un navigateur (v2)
    error: str | None = None


class SnapshotPayload(BaseModel):
    """Charge utile stockée pour chaque snapshot de page (append-only)."""

    url: str
    canonical_url: str
    fetched_at: datetime = Field(default_factory=utcnow)
    status_code: int
    title: str
    page_type: str
    page_type_score: float
    text_hash: str
    html_hash: str
    word_count: int
    structured: dict = Field(default_factory=dict)  # extraction + entités sérialisées
    seo: dict = Field(default_factory=dict)
    technologies: list = Field(default_factory=list)
    health: dict = Field(default_factory=dict)
    links: list = Field(default_factory=list)
    images: list = Field(default_factory=list)
