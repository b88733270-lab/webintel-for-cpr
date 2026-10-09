"""Routeur système : santé et utilisation."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from webintel import __version__
from webintel.api.deps import api_key_auth, get_db
from webintel.core.enums import JobStatus
from webintel.core.utils import utcnow
from webintel.database.models import Alert, CrawlJob, Entity, Event, PageSnapshot, Project
from webintel.jobs.scheduler import Scheduler

router = APIRouter()

_scheduler_state: dict = {"scheduler": None}


def set_scheduler(scheduler: Scheduler | None) -> None:
    """Mémorise le planificateur démarré par le lifespan (pour /api/health)."""
    _scheduler_state["scheduler"] = scheduler


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """État de l'application : version, base de données, planificateur."""
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "erreur"
    scheduler = _scheduler_state.get("scheduler")
    if scheduler is None:
        scheduler_status = "inactif"
    else:
        thread = getattr(scheduler, "_thread", None)
        scheduler_status = "actif" if (thread is not None and thread.is_alive()) else "inactif"
    return {
        "status": "ok",
        "version": __version__,
        "db": db_status,
        "scheduler": scheduler_status,
    }


@router.get("/usage")
def usage(
    db: Session = Depends(get_db), api_key=Depends(api_key_auth)
) -> dict:
    """Compteurs d'utilisation.

    En mode clé API, les compteurs sont cloisonnés au projet de la clé
    (P0-1) ; en mode bypass, ils couvrent tous les projets (v1).
    `/api/health` reste volontairement ouvert (sondes).
    """
    pid = api_key.project_id if api_key is not None else None
    since_24h = utcnow().replace(tzinfo=None) - timedelta(hours=24)

    def _count(model) -> int:
        query = db.query(func.count(model.id))
        if pid is not None:
            column = Project.id if model is Project else model.project_id
            query = query.filter(column == pid)
        return query.scalar() or 0

    events_q = db.query(func.count(Event.id)).filter(Event.created_at >= since_24h)
    jobs_q = db.query(func.count(CrawlJob.id)).filter(
        CrawlJob.status == JobStatus.RUNNING.value
    )
    if pid is not None:
        events_q = events_q.filter(Event.project_id == pid)
        jobs_q = jobs_q.filter(CrawlJob.project_id == pid)

    return {
        "projects": _count(Project),
        "entities": _count(Entity),
        "snapshots": _count(PageSnapshot),
        "events_24h": events_q.scalar() or 0,
        "alerts": _count(Alert),
        "jobs_running": jobs_q.scalar() or 0,
    }
