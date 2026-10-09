"""Routeur moniteurs : CRUD + exécution manuelle.

Isolation (P0-1) : en mode clé API, chaque opération est cloisonnée au
projet de la clé (`require_project_access` / `scoped_project_id`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from webintel.api import schemas
from webintel.api.deps import (
    api_key_auth,
    get_db,
    get_project_or_404,
    require_project_access,
    scoped_project_id,
)
from webintel.database.models import Monitor
from webintel.jobs.manager import JobContext, get_manager
from webintel.monitoring.service import run_monitor

router = APIRouter()


def _get_monitor_or_404(db: Session, monitor_id: int) -> Monitor:
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(
            status_code=404, detail=f"Moniteur introuvable : id={monitor_id}."
        )
    return monitor


@router.post("/monitors", response_model=schemas.MonitorOut, status_code=status.HTTP_201_CREATED)
def create_monitor(
    body: schemas.MonitorCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> Monitor:
    """Crée un moniteur de surveillance (dans le projet de la clé)."""
    project_id = scoped_project_id(api_key, body.project_id)
    get_project_or_404(db, project_id)
    monitor = Monitor(
        project_id=project_id,
        name=body.name.strip(),
        target_type=body.target_type,
        target=body.target.strip(),
        interval_minutes=body.interval_minutes,
        active=True,
        config=dict(body.config or {}),
    )
    db.add(monitor)
    db.commit()
    db.refresh(monitor)
    return monitor


@router.get("/monitors", response_model=list[schemas.MonitorOut])
def list_monitors(
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[Monitor]:
    """Liste les moniteurs (en mode clé : uniquement ceux du projet de la clé)."""
    project_id = scoped_project_id(api_key, project_id)
    query = db.query(Monitor).order_by(Monitor.id.desc())
    if project_id is not None:
        query = query.filter(Monitor.project_id == project_id)
    return query.limit(200).all()


@router.get("/monitors/{monitor_id}", response_model=schemas.MonitorOut)
def get_monitor(
    monitor_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> Monitor:
    """Détail d'un moniteur."""
    monitor = _get_monitor_or_404(db, monitor_id)
    require_project_access(api_key, monitor.project_id)
    return monitor


@router.delete("/monitors/{monitor_id}")
def delete_monitor(
    monitor_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Supprime un moniteur."""
    monitor = _get_monitor_or_404(db, monitor_id)
    require_project_access(api_key, monitor.project_id)
    db.delete(monitor)
    db.commit()
    return {"id": monitor_id, "deleted": True}


@router.post("/monitors/{monitor_id}/run")
def run_monitor_now(
    monitor_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Lance immédiatement l'exécution d'un moniteur (job asynchrone)."""

    def runner(ctx: JobContext, session: Session) -> None:
        ctx.update_progress(phase="collecte")
        result = run_monitor(session, monitor_id)
        session.commit()
        ctx.update_progress(
            phase="terminé",
            urls_checked=result.get("urls_checked", 0),
            events=len(result.get("events", [])),
            alerts=len(result.get("alerts", [])),
        )

    monitor = _get_monitor_or_404(db, monitor_id)
    # Contrôle AVANT toute soumission de job.
    require_project_access(api_key, monitor.project_id)
    job_id = get_manager().submit(
        "monitor_run", monitor.project_id, {"monitor_id": monitor_id}, runner=runner
    )
    return {"job_id": job_id, "monitor_id": monitor_id, "message": "Exécution du moniteur lancée."}
