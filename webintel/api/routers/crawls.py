"""Routeur crawls : soumission et pilotage des jobs de crawl."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webintel.api import schemas
from webintel.api.deps import (
    api_key_auth,
    get_db,
    get_project_or_404,
    require_project_access,
    scoped_project_id,
)
from webintel.core.pipeline import analyze_fetch_result
from webintel.crawler.engine import CrawlEngine
from webintel.database.base import SessionLocal
from webintel.database.models import CrawlJob
from webintel.jobs.manager import JobContext, get_manager

router = APIRouter()


def _normalize_start_urls(body: schemas.CrawlCreate) -> list[str]:
    """Normalise `start_url` / `start_urls` / `domain` en liste d'URLs de départ."""
    if body.start_urls:
        urls = [u for u in body.start_urls if u and u.strip()]
    elif body.start_url and body.start_url.strip():
        urls = [body.start_url.strip()]
    elif body.domain and body.domain.strip():
        domain = body.domain.strip().rstrip("/")
        if "://" not in domain:
            domain = "https://" + domain
        urls = [domain]
    else:
        raise HTTPException(
            status_code=422,
            detail="Fournissez au moins une URL de départ (start_url, start_urls) ou un domaine.",
        )
    return urls


@router.post("/crawls", response_model=schemas.CrawlOut)
def create_crawl(
    body: schemas.CrawlCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Lance un crawl borné (job asynchrone). Retourne `{id, status, progress}`."""
    project_id = scoped_project_id(api_key, body.project_id)
    get_project_or_404(db, project_id)
    start_urls = _normalize_start_urls(body)
    limits = (body.limits.model_dump(exclude_none=True) if body.limits else {})

    def runner(ctx: JobContext, session: Session) -> None:
        engine = CrawlEngine()

        def _page_pipeline(fetch_result) -> None:
            """Pipeline canonique par page crawlée (session dédiée au thread)."""
            page_session = SessionLocal()
            try:
                analyze_fetch_result(page_session, project_id, fetch_result)
            finally:
                page_session.close()

        result = engine.run(
            str(ctx.job_id),
            start_urls=start_urls,
            project_id=project_id,
            limits=limits,
            progress_cb=lambda p: ctx.update_progress(
                **{**p, "urls": list((p.get("urls") or []))[-20:]}
            ),
            ctx=ctx,
            page_pipeline=_page_pipeline,
        )
        ctx.update_progress(status="done", summary={
            "visited": result.get("visited"),
            "discovered": result.get("discovered"),
            "errors": result.get("errors"),
            "elapsed_s": result.get("elapsed_s"),
        })

    job_id = get_manager().submit(
        "crawl", project_id,
        {"start_urls": start_urls, "limits": limits},
        runner=runner,
    )
    state = get_manager().status(job_id)
    return {"id": state["id"], "status": state["status"], "progress": state["progress"]}


@router.get("/crawls", response_model=list[schemas.CrawlDetailOut])
def list_crawls(
    project_id: int | None = None,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[dict]:
    """Liste les jobs (crawls, exécutions de moniteurs...), filtrables par projet."""
    project_id = scoped_project_id(api_key, project_id)
    query = db.query(CrawlJob).order_by(CrawlJob.id.desc())
    if project_id is not None:
        query = query.filter(CrawlJob.project_id == project_id)
    query = query.limit(100)
    return [
        {
            "id": j.id, "kind": j.kind, "status": j.status,
            "progress": dict(j.progress or {}), "error": j.error,
            "created_at": j.created_at, "updated_at": j.updated_at,
        }
        for j in query.all()
    ]


def _get_job_or_404(db: Session, job_id: int) -> CrawlJob:
    job = db.get(CrawlJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job introuvable : id={job_id}.")
    return job


@router.get("/crawls/{job_id}", response_model=schemas.CrawlDetailOut)
def get_crawl(
    job_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """État courant d'un job (via le gestionnaire + la ligne DB)."""
    job = _get_job_or_404(db, job_id)
    require_project_access(api_key, job.project_id)
    try:
        state = get_manager().status(job_id)
    except KeyError:
        state = {
            "id": job.id, "kind": job.kind, "status": job.status,
            "progress": dict(job.progress or {}), "error": job.error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
    return {
        "id": state["id"], "kind": state["kind"], "status": state["status"],
        "progress": state["progress"], "error": state["error"],
        "created_at": state["created_at"], "updated_at": state["updated_at"],
    }


def _control(db: Session, api_key, job_id: int, action: str) -> dict:
    job = _get_job_or_404(db, job_id)
    # Contrôle d'accès AVANT tout pilotage du job.
    require_project_access(api_key, job.project_id)
    manager = get_manager()
    try:
        manager.status(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job introuvable : id={job_id}.")
    {"cancel": manager.cancel, "pause": manager.pause, "resume": manager.resume}[action](job_id)
    state = manager.status(job_id)
    return {"id": state["id"], "status": state["status"], "progress": state["progress"]}


@router.post("/crawls/{job_id}/cancel", response_model=schemas.CrawlOut)
def cancel_crawl(
    job_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Annule un job de crawl."""
    return _control(db, api_key, job_id, "cancel")


@router.post("/crawls/{job_id}/pause", response_model=schemas.CrawlOut)
def pause_crawl(
    job_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Met un job de crawl en pause."""
    return _control(db, api_key, job_id, "pause")


@router.post("/crawls/{job_id}/resume", response_model=schemas.CrawlOut)
def resume_crawl(
    job_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Reprend un job de crawl en pause."""
    return _control(db, api_key, job_id, "resume")
