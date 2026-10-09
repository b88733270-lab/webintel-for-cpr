"""Routeur recherche plein champ : GET /api/search -> {results, count}."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from webintel.api.deps import api_key_auth, get_db, scoped_project_id
from webintel.search.engine import search_entities

router = APIRouter()


@router.get("/search")
def search(
    q: str | None = Query(default=None, max_length=200),
    type: str | None = Query(default=None, alias="type"),
    min_price: float | None = Query(default=None),
    max_price: float | None = Query(default=None),
    domain: str | None = Query(default=None),
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Recherche globale d'entités (mêmes filtres que /api/entities, cloisonnée)."""
    project_id = scoped_project_id(api_key, project_id)
    results = search_entities(
        db, project_id=project_id, q=q, entity_type=type,
        min_price=min_price, max_price=max_price, domain=domain,
        limit=limit,
    )
    return {"results": results, "count": len(results)}
