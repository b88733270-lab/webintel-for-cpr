"""Routeur entités : recherche, détail, historique d'observations."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from webintel.api import schemas
from webintel.api.deps import (
    api_key_auth,
    get_db,
    require_project_access,
    scoped_project_id,
)
from webintel.database.models import Entity, Observation, Source
from webintel.entities.registry import get_observations_by_source
from webintel.search.engine import search_entities

router = APIRouter()


def _source_names(db: Session, source_ids: set[int]) -> dict[int, str]:
    """Noms des sources pour un ensemble d'ids (une seule requête)."""
    ids = {i for i in source_ids if i}
    if not ids:
        return {}
    return {
        row.id: row.name
        for row in db.query(Source).filter(Source.id.in_(ids)).all()
    }


@router.get("/entities")
def list_entities(
    project_id: int | None = Query(default=None),
    type: str | None = Query(default=None, alias="type"),
    q: str | None = Query(default=None),
    min_price: float | None = Query(default=None),
    max_price: float | None = Query(default=None),
    domain: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Recherche d'entités avec filtres (via `search.engine`, cloisonnée au projet)."""
    project_id = scoped_project_id(api_key, project_id)
    results = search_entities(
        db, project_id=project_id, q=q, entity_type=type,
        min_price=min_price, max_price=max_price, domain=domain,
        since=since, limit=limit,
    )
    return {"results": results, "count": len(results)}


def _get_entity_or_404(db: Session, entity_id: int) -> Entity:
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Entité introuvable : id={entity_id}.")
    return entity


@router.get("/entities/{entity_id}", response_model=schemas.EntityOut)
def get_entity(
    entity_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> schemas.EntityOut:
    """Détail d'une entité + ses observations groupées par source (P0-3).

    Discipline v1.1.1 : `data` = identité/synthèse uniquement ; les valeurs
    volatiles (prix, disponibilité...) sont dans `observations_by_source`,
    attribuées à leur source (`source_id`, `source_name`).
    """
    entity = _get_entity_or_404(db, entity_id)
    require_project_access(api_key, entity.project_id)
    grouped = get_observations_by_source(db, entity_id)
    source_ids = {
        row.source_id for rows in grouped.values() for row in rows
        if row.source_id
    }
    names = _source_names(db, source_ids)
    return schemas.EntityOut.from_row(
        entity, observations_by_source=grouped, source_names=names
    )


@router.get("/entities/{entity_id}/history", response_model=list[schemas.ObservationOut])
def entity_history(
    entity_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[schemas.ObservationOut]:
    """Historique des changements d'une entité, avec provenance (v1.1.1).

    Chaque changement est attribué à la source qui l'a produit
    (`source_id`, `source_name`, `source_url`, `source_domain`).
    Ordre antichronologique.
    """
    entity = _get_entity_or_404(db, entity_id)
    require_project_access(api_key, entity.project_id)
    rows = (
        db.query(Observation)
        .filter(Observation.entity_id == entity_id)
        .order_by(Observation.observed_at.desc(), Observation.id.desc())
        .limit(limit)
        .all()
    )
    names = _source_names(db, {r.source_id for r in rows if r.source_id})
    return [
        schemas.ObservationOut(
            id=r.id,
            entity_id=r.entity_id,
            field=r.field,
            old_value=r.old_value,
            new_value=r.new_value,
            observed_at=r.observed_at,
            snapshot_id=r.snapshot_id,
            source_id=r.source_id,
            source_name=names.get(r.source_id) if r.source_id else None,
            source_url=r.source_url or "",
            source_domain=r.source_domain or "",
        )
        for r in rows
    ]
