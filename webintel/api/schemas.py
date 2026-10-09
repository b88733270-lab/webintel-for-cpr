"""DTOs Pydantic v2 de l'API REST (contrat §10 d'architecture.md).

Tous les champs et messages exposés côté client sont en français dans la
mesure où ils décrivent des libellés (les codes techniques restent en
anglais : `product`, `price_drop`, ...).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Projets & clés API
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ProjectOut(BaseModel):
    id: int
    name: str
    created_at: datetime


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    quotas: dict = Field(default_factory=dict)


class ApiKeyCreatedOut(BaseModel):
    """Réponse de création : la clé en clair n'apparaît QUE ici, une seule fois."""

    id: int
    name: str
    prefix: str
    plain_key: str
    message: str = "Copiez cette clé maintenant : elle ne sera plus jamais affichée."


class ApiKeyOut(BaseModel):
    """Liste des clés (jamais la clé en clair)."""

    id: int
    name: str
    prefix: str
    quotas: dict
    revoked: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Analyse
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    url: str
    project_id: int | None = None
    keep_html: bool = False


class AnalyzeResponse(BaseModel):
    """Fiche intelligence — reprend le SnapshotPayload + entités, prix, contacts,
    technologies, SEO, santé, diff/événements (re-analyse) et score qualité."""

    project_id: int
    url: str
    final_url: str
    status_code: int
    duration_ms: int
    page_type: str
    page_type_score: float
    title: str
    description: str | None = None
    entities: list[dict] = Field(default_factory=list)
    prices: list[dict] = Field(default_factory=list)
    contacts: dict = Field(default_factory=dict)
    technologies: list[dict] = Field(default_factory=list)
    seo: dict = Field(default_factory=dict)
    health: dict = Field(default_factory=dict)
    structured_summary: dict = Field(default_factory=dict)
    links: list[dict] = Field(default_factory=list)
    images: list[dict] = Field(default_factory=list)
    word_count: int = 0
    needs_js: bool = False
    snapshot_id: int
    previous_snapshot_id: int | None = None
    diff: dict | None = None
    events: list[dict] = Field(default_factory=list)
    quality: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Crawls (jobs)
# ---------------------------------------------------------------------------

class CrawlLimits(BaseModel):
    max_pages: int | None = None
    max_depth: int | None = None
    delay_s: float | None = None
    concurrency: int | None = None
    allow_domains: list[str] | None = None
    exclude_paths: list[str] | None = None
    obey_robots: bool | None = None


class CrawlCreate(BaseModel):
    project_id: int
    start_url: str | None = None
    start_urls: list[str] | None = None
    domain: str | None = None
    limits: CrawlLimits | None = None


class CrawlOut(BaseModel):
    id: int
    status: str
    progress: dict = Field(default_factory=dict)


class CrawlDetailOut(BaseModel):
    id: int
    kind: str
    status: str
    progress: dict = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Moniteurs
# ---------------------------------------------------------------------------

class MonitorCreate(BaseModel):
    project_id: int
    name: str = Field(min_length=1, max_length=255)
    target_type: str = Field(pattern=r"^(url|domain|product|price|keyword)$")
    target: str = Field(min_length=1)
    interval_minutes: int = Field(ge=1)
    config: dict = Field(default_factory=dict)


class MonitorOut(BaseModel):
    id: int
    project_id: int
    name: str
    target_type: str
    target: str
    interval_minutes: int
    active: bool
    config: dict
    last_run_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Événements, alertes, entités
# ---------------------------------------------------------------------------

class EventOut(BaseModel):
    id: int
    project_id: int
    type: str
    entity_id: int | None = None
    url: str
    before: dict | None = None
    after: dict | None = None
    importance: str
    evidence: dict
    created_at: datetime


class AlertOut(BaseModel):
    id: int
    project_id: int
    monitor_id: int | None = None
    rule: dict
    channel: str
    payload: dict
    status: str
    error: str | None = None
    created_at: datetime


class SourceObservationOut(BaseModel):
    """Observation d'une valeur par source (P0-3, durci v1.1.1 : `source_id`)."""

    id: int
    entity_id: int
    source_id: int | None = None
    source_name: str | None = None
    source_url: str
    source_domain: str
    field: str
    value: Any | None = None
    confidence: float = 1.0
    observed_at: datetime
    snapshot_id: int | None = None

    @classmethod
    def from_row(cls, row, source_name: str | None = None) -> "SourceObservationOut":
        return cls(
            id=row.id,
            entity_id=row.entity_id,
            source_id=row.source_id,
            source_name=source_name,
            source_url=row.source_url or "",
            source_domain=row.source_domain or "",
            field=row.field or "",
            value=row.value,
            confidence=float(row.confidence or 0.0),
            observed_at=row.observed_at,
            snapshot_id=row.snapshot_id,
        )


class EntityOut(BaseModel):
    id: int
    project_id: int
    entity_key: str
    entity_type: str
    name: str
    # Discipline v1.1.1 : identité/synthèse UNIQUEMENT — jamais de prix,
    # disponibilité ou note (ces valeurs vivent dans `observations_by_source`).
    data: dict
    first_seen: datetime
    last_seen: datetime
    sources: list
    history_url: str
    # Observations par source (P0-3) : {hôte: [observation, ...]}
    # (dernière valeur connue par champ, groupée par hôte source).
    observations_by_source: dict[str, list[SourceObservationOut]] = Field(default_factory=dict)

    @classmethod
    def from_row(
        cls,
        row,
        observations_by_source: dict | None = None,
        source_names: dict[int, str] | None = None,
    ) -> "EntityOut":
        names = source_names or {}
        obs: dict[str, list[SourceObservationOut]] = {}
        for domain, rows in (observations_by_source or {}).items():
            obs[str(domain)] = [
                SourceObservationOut.from_row(
                    r, names.get(r.source_id) if r.source_id else None
                )
                for r in rows
            ]
        return cls(
            id=row.id,
            project_id=row.project_id,
            entity_key=row.entity_key,
            entity_type=row.entity_type,
            name=row.name,
            data=row.data or {},
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            sources=row.sources or [],
            history_url=f"/api/entities/{row.id}/history",
            observations_by_source=obs,
        )


class ObservationOut(BaseModel):
    id: int
    entity_id: int
    field: str
    old_value: dict | None = None
    new_value: dict | None = None
    observed_at: datetime
    snapshot_id: int | None = None
    # Provenance directe (v1.1.1) : la source qui a produit le changement.
    source_id: int | None = None
    source_name: str | None = None
    source_url: str = ""
    source_domain: str = ""


class SourceOut(BaseModel):
    """Source d'observation (v1.1.1)."""

    id: int
    name: str
    canonical_host: str
    country: str | None = None
    seller: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    observation_count: int = 0
    last_observed_at: datetime | None = None

    @classmethod
    def from_row(cls, row, observation_count: int = 0,
                 last_observed_at: datetime | None = None) -> "SourceOut":
        return cls(
            id=row.id,
            name=row.name,
            canonical_host=row.canonical_host,
            country=row.country,
            seller=row.seller,
            metadata=row.meta or {},
            created_at=row.created_at,
            observation_count=observation_count,
            last_observed_at=last_observed_at,
        )


class SourceDetailOut(SourceOut):
    """Détail d'une source + ses observations (paginées côté routeur)."""

    observations: list[SourceObservationOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Recherche & exports
# ---------------------------------------------------------------------------

class SearchQuery(BaseModel):
    q: str | None = None
    type: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    domain: str | None = None
    project_id: int | None = None


class ExportCreate(BaseModel):
    project_id: int
    kind: str = Field(pattern=r"^(entities|events|snapshots|monitors|observations)$")
    format: str = Field(pattern=r"^(json|csv|xlsx)$")
    filters: dict = Field(default_factory=dict)


class ExportOut(BaseModel):
    id: int
    download_url: str
