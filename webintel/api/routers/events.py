"""Routeur événements : GET /api/events (filtres project_id, type, since)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from webintel.api import schemas
from webintel.api.deps import api_key_auth, get_db, scoped_project_id
from webintel.database.models import Event

router = APIRouter()


@router.get("/events", response_model=list[schemas.EventOut])
def list_events(
    project_id: int | None = Query(default=None),
    type: str | None = Query(default=None, alias="type"),
    since: datetime | None = Query(default=None, description="ISO 8601"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[Event]:
    """Liste les événements, les plus récents d'abord (cloisonnés au projet)."""
    project_id = scoped_project_id(api_key, project_id)
    query = db.query(Event).order_by(Event.created_at.desc(), Event.id.desc())
    if project_id is not None:
        query = query.filter(Event.project_id == project_id)
    if type:
        query = query.filter(Event.type == type.strip().lower())
    if since is not None:
        naive = since.replace(tzinfo=None) if since.tzinfo else since
        query = query.filter(Event.created_at >= naive)
    return query.limit(limit).all()
