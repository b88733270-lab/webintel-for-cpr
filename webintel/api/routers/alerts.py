"""Routeur alertes : GET /api/alerts (filtre project_id)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from webintel.api import schemas
from webintel.api.deps import api_key_auth, get_db, scoped_project_id
from webintel.database.models import Alert

router = APIRouter()


@router.get("/alerts", response_model=list[schemas.AlertOut])
def list_alerts(
    project_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[Alert]:
    """Liste les alertes envoyées, les plus récentes d'abord (cloisonnées au projet)."""
    project_id = scoped_project_id(api_key, project_id)
    query = db.query(Alert).order_by(Alert.created_at.desc(), Alert.id.desc())
    if project_id is not None:
        query = query.filter(Alert.project_id == project_id)
    return query.limit(limit).all()
