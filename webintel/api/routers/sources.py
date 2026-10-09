"""Routeur sources : inventaire des sources d'observation (v1.1.1).

Une `Source` (boutique, site, vendeur) n'existe que par les observations
qu'elle a produites : les deux endpoints sont cloisonnés au projet de la
clé API — une source sans observation dans ce projet répond 404 (aucune
fuite inter-projets).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from webintel.api import schemas
from webintel.api.deps import api_key_auth, get_db, scoped_project_id
from webintel.database.models import Entity, Source, SourceObservation

router = APIRouter()


@router.get("/sources", response_model=list[schemas.SourceOut])
def list_sources(
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[schemas.SourceOut]:
    """Sources ayant produit ≥ 1 observation dans le projet (ordre alphabétique)."""
    project_id = scoped_project_id(api_key, project_id)
    rows = (
        db.query(
            Source,
            func.count(SourceObservation.id).label("observation_count"),
            func.max(SourceObservation.observed_at).label("last_observed_at"),
        )
        .join(SourceObservation, SourceObservation.source_id == Source.id)
        .join(Entity, Entity.id == SourceObservation.entity_id)
        .filter(Entity.project_id == project_id)
        .group_by(Source.id)
        .order_by(Source.name)
        .all()
    )
    return [
        schemas.SourceOut.from_row(
            source,
            observation_count=int(count or 0),
            last_observed_at=last_seen,
        )
        for source, count, last_seen in rows
    ]


@router.get("/sources/{source_id}", response_model=schemas.SourceDetailOut)
def get_source(
    source_id: int,
    project_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> schemas.SourceDetailOut:
    """Détail d'une source + ses observations dans le projet.

    404 si la source n'a aucune observation dans le projet de la clé
    (cloisonnement : on ne révèle pas les sources des autres projets).
    """
    project_id = scoped_project_id(api_key, project_id)
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source introuvable : id={source_id}.")

    obs_rows = (
        db.query(SourceObservation)
        .join(Entity, Entity.id == SourceObservation.entity_id)
        .filter(
            SourceObservation.source_id == source_id,
            Entity.project_id == project_id,
        )
        .order_by(SourceObservation.observed_at.desc(), SourceObservation.id.desc())
        .limit(limit)
        .all()
    )
    if not obs_rows:
        raise HTTPException(
            status_code=404,
            detail=f"Source introuvable pour ce projet : id={source_id}.",
        )

    observations = [
        schemas.SourceObservationOut.from_row(obs, source.name) for obs in obs_rows
    ]

    count = (
        db.query(func.count(SourceObservation.id))
        .join(Entity, Entity.id == SourceObservation.entity_id)
        .filter(
            SourceObservation.source_id == source_id,
            Entity.project_id == project_id,
        )
        .scalar()
    )
    last_seen = (
        db.query(func.max(SourceObservation.observed_at))
        .join(Entity, Entity.id == SourceObservation.entity_id)
        .filter(
            SourceObservation.source_id == source_id,
            Entity.project_id == project_id,
        )
        .scalar()
    )
    detail = schemas.SourceDetailOut.from_row(
        source, observation_count=int(count or 0), last_observed_at=last_seen
    )
    detail.observations = observations
    return detail
