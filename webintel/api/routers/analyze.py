"""Routeur analyse : POST /api/analyze -> fiche intelligence."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webintel.api import pipeline, schemas
from webintel.api.deps import api_key_auth, get_db, get_project_or_404, scoped_project_id
from webintel.core.exceptions import FetchError, SecurityError
from webintel.database.models import Project

router = APIRouter()


@router.post("/analyze", response_model=schemas.AnalyzeResponse)
def analyze(
    body: schemas.AnalyzeRequest,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Analyse une URL et retourne la fiche intelligence complète.

    - `SecurityError` -> 422 (URL refusée, message FR) ;
    - `FetchError` -> 502 (échec de récupération, message FR) ;
    - en mode clé API, le `project_id` est celui de la clé (403 s'il diffère).
    """
    project_id = scoped_project_id(api_key, body.project_id)
    if project_id is None:
        # Mode bypass : projet par défaut (le premier existant, sinon créé).
        project = db.query(Project).order_by(Project.id).first()
        if project is None:
            project = Project(name="Défaut")
            db.add(project)
            db.commit()
            db.refresh(project)
        project_id = project.id
    else:
        get_project_or_404(db, project_id)

    try:
        return pipeline.analyze_url(db, project_id, body.url, keep_html=body.keep_html)
    except SecurityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
