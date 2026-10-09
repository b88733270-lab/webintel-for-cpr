"""Routeur API des workflows de recherche — Phase 7 (backend).

Expose le workflow ``analyse_plante`` (7 étapes : plante → constituants →
publications → molécule → simulation → rapport → export) :

- ``POST /science/workflows`` : création (201) ;
- ``GET /science/workflows`` : liste cloisonnée au projet ;
- ``GET /science/workflows/{id}`` : détail + étapes ;
- ``POST /science/workflows/{id}/avancer`` : exécute la prochaine étape (200) ;
- ``POST /science/workflows/{id}/rejouer`` : réinitialise depuis les mêmes
  inputs (200, ``input_hash`` inchangé).

Authentification : ``api_key_auth`` sur chaque route (même pattern que
``routers/science.py``). Cloisonnement via ``scoped_project_id`` /
``require_project_access`` : 403 vers un projet étranger en mode serveur,
401 sans clé.

Erreurs (messages français) : run inconnu → 404 ; inputs invalides → 422 ;
étape impossible (workflow terminé, échec d'étape) → 409.

Note : en cas d'échec d'étape, l'état (étape ``echouee`` + message) est
committé AVANT la réponse 409, pour que la reprise
(``POST .../avancer``) reste possible.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    require_project_access,
    scoped_project_id,
)
from webintel.science import workflows as wf

router = APIRouter()


# ---------------------------------------------------------------------------
# Schémas
# ---------------------------------------------------------------------------

class WorkflowCreate(BaseModel):
    """Corps de ``POST /science/workflows``."""

    type_workflow: str = Field(min_length=1, max_length=64)
    nom: str = Field(min_length=1, max_length=255)
    inputs: dict = Field(default_factory=dict)
    project_id: int | None = None


class WorkflowStepOut(BaseModel):
    """Une étape du workflow : nom, statut, entrées, sorties, erreur."""

    nom: str
    libelle: str
    statut: str
    entrees: dict
    sorties: dict
    erreur: str | None = None


class WorkflowOut(BaseModel):
    """Run de workflow : état global + journal des 7 étapes."""

    id: int
    project_id: int
    type_workflow: str
    nom: str
    etapes: list[WorkflowStepOut]
    etape_courante: int
    statut: str
    input_hash: str
    resultat: dict | None = None
    cree_par: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _serialiser(run: wf.WorkflowRun) -> WorkflowOut:
    """Convertit un ``WorkflowRun`` en schéma de sortie."""
    return WorkflowOut(
        id=run.id,
        project_id=run.project_id,
        type_workflow=run.type_workflow,
        nom=run.nom,
        etapes=[
            WorkflowStepOut(
                nom=e.get("nom", ""),
                libelle=e.get("libelle", ""),
                statut=e.get("statut", ""),
                entrees=dict(e.get("entrees") or {}),
                sorties=dict(e.get("sorties") or {}),
                erreur=e.get("erreur"),
            )
            for e in (run.etapes or [])
        ],
        etape_courante=run.etape_courante,
        statut=run.statut,
        input_hash=run.input_hash,
        resultat=run.resultat,
        cree_par=run.cree_par,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _run_ou_404(db: Session, run_id: int, api_key) -> wf.WorkflowRun:
    """Retourne le run ``run_id`` (404 si inconnu, 403 si projet étranger)."""
    try:
        run = wf.get_workflow(db, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    require_project_access(api_key, run.project_id)
    return run


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/science/workflows", response_model=WorkflowOut, status_code=201)
def creer_workflow(
    payload: WorkflowCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> WorkflowOut:
    """Crée un run de workflow (7 étapes en attente) dans le projet autorisé.

    Le ``project_id`` est résolu par ``scoped_project_id`` (cloisonnement
    par défaut sur le projet de la clé API). Inputs invalides → 422.
    """
    project_id = scoped_project_id(api_key, payload.project_id)
    if project_id is None:
        raise HTTPException(
            status_code=400,
            detail="Le champ project_id est requis pour créer un workflow.",
        )
    try:
        run = wf.creer_workflow(
            db,
            project_id=project_id,
            type_workflow=payload.type_workflow,
            nom=payload.nom,
            inputs=payload.inputs,
            cree_par=getattr(api_key, "name", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(run)
    return _serialiser(run)


@router.get("/science/workflows", response_model=list[WorkflowOut])
def lister_workflows(
    project_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[WorkflowOut]:
    """Liste les runs de workflows du projet autorisé (ordre de création).

    Sans ``project_id``, le projet de la clé API est utilisé
    (cloisonnement). ``require_project_access`` est appliqué sur le projet
    demandé.
    """
    effectif = scoped_project_id(api_key, project_id)
    if effectif is not None:
        require_project_access(api_key, effectif)
    runs = wf.list_workflows(db, effectif, limit=limit, offset=offset)
    return [_serialiser(r) for r in runs]


@router.get("/science/workflows/{run_id}", response_model=WorkflowOut)
def detail_workflow(
    run_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> WorkflowOut:
    """Détail d'un run : état global + journal des étapes (404 si inconnu)."""
    return _serialiser(_run_ou_404(db, run_id, api_key))


@router.post("/science/workflows/{run_id}/avancer", response_model=WorkflowOut)
def avancer_workflow(
    run_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> WorkflowOut:
    """Exécute la prochaine étape non terminée du run (200).

    En cas d'échec d'étape, l'état (étape ``echouee`` + message en
    français) est persisté et la réponse est 409 : un nouvel appel reprend
    à l'étape en attente. Workflow déjà terminé → 409.
    """
    run = _run_ou_404(db, run_id, api_key)
    try:
        run = wf.avancer_workflow(db, run.id)
    except ValueError as exc:
        # Persiste l'étape échouée AVANT la 409 : la reprise reste possible.
        db.commit()
        db.refresh(run)
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(run)
    return _serialiser(run)


@router.post("/science/workflows/{run_id}/rejouer", response_model=WorkflowOut)
def rejouer_workflow(
    run_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> WorkflowOut:
    """Réinitialise le run depuis les mêmes inputs initiaux (200).

    Les 7 étapes repartent à ``en_attente``, le ``resultat`` est effacé et
    le ``input_hash`` reste identique (mêmes inputs).
    """
    run = _run_ou_404(db, run_id, api_key)
    try:
        run = wf.rejouer_workflow(db, run.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(run)
    return _serialiser(run)
