"""Routeur API du workflow de docking (v2.1).

- ``GET /api/science/docking/statut`` : disponibilité réelle du moteur
  (jamais de faux positif).
- ``POST /api/science/docking/apercu`` : aperçu du workflow en 9
  étapes (validation des entrées + paramètres) SANS calcul —
  ``scores = None`` + message explicite.
- ``POST /api/science/docking/executer`` : exécution réelle via le
  moteur si disponible (job persisté ``statut="termine"``) ; sinon
  refus propre (422, job ``statut="refuse"``, aucun faux score).
- ``GET /api/science/docking`` : jobs du projet · ``GET .../{id}`` :
  détail.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    limite_debit_couteux,
    projet_effectif_ou_404,
    require_project_access,
)
from webintel.science import docking as dock

router = APIRouter(prefix="/api/science/docking")


def _identite(api_key) -> str:
    if api_key is None:
        return "local"
    return f"clé:{getattr(api_key, 'prefix', None) or '?'}"


class DockingApercuIn(BaseModel):
    """Entrées pour l'aperçu (validées, aucun calcul)."""

    recepteur_pdb: str = Field(min_length=1)
    ligand_smiles: str = Field(min_length=1)
    parametres: dict = Field(default_factory=dict)
    recepteur_nom: str | None = Field(default=None, max_length=255)
    ligand_nom: str | None = Field(default=None, max_length=255)


class DockingExecuterIn(DockingApercuIn):
    """Exécution : mêmes entrées + nom du job + projet."""

    nom: str = Field(min_length=1, max_length=255)
    project_id: int | None = None


class DockingJobOut(BaseModel):
    """Job de docking exposé (résultat = prédiction ou refus explicite)."""

    id: int
    project_id: int
    nom: str
    statut: str
    data_level: str
    moteur: str | None
    moteur_version: str | None
    inputs_hash: str
    model_config = {"from_attributes": True}


def _job_ou_404(db: Session, job_id: int, api_key):
    try:
        job = dock.get_docking_job(db, job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    require_project_access(api_key, job.project_id)
    return job


@router.get("/statut")
def statut_moteur(api_key=Depends(api_key_auth)) -> dict:
    """Disponibilité réelle du moteur de docking (vérifiée à chaque appel)."""
    disponible, raison = dock.statut_moteur()
    return {
        "moteur": dock.MOTEUR_ATTENDU,
        "disponible": disponible,
        "raison": raison if not disponible else None,
        "etapes": [{"etape": nom, "description": desc}
                   for nom, desc in dock.ETAPES],
    }


@router.post("/apercu")
def apercu(
    payload: DockingApercuIn,
    api_key=Depends(api_key_auth),
) -> dict:
    """Aperçu du workflow : étapes + paramètres validés, SANS calcul.

    ``scores`` vaut toujours ``None`` : aucun faux score n'est produit.
    Entrées invalides → 422 explicite.
    """
    try:
        return dock.apercu_workflow(
            recepteur_pdb=payload.recepteur_pdb,
            ligand_smiles=payload.ligand_smiles,
            parametres=payload.parametres,
            recepteur_nom=payload.recepteur_nom,
            ligand_nom=payload.ligand_nom,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/executer", response_model=DockingJobOut, status_code=201)
def executer(
    payload: DockingExecuterIn,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
    _limite: None = Depends(limite_debit_couteux),
) -> DockingJobOut:
    """Exécute le docking via le moteur (ou refuse proprement).

    Moteur indisponible → 422 explicite (un job ``statut="refuse"`` est
    persisté pour l'audit, SANS score). Entrées invalides → 422.
    """
    project_id = projet_effectif_ou_404(db, api_key, payload.project_id)
    try:
        job = dock.executer_docking(
            db,
            project_id=project_id,
            nom=payload.nom,
            recepteur_pdb=payload.recepteur_pdb,
            ligand_smiles=payload.ligand_smiles,
            parametres=payload.parametres,
            recepteur_nom=payload.recepteur_nom,
            ligand_nom=payload.ligand_nom,
            execute_par=_identite(api_key),
        )
    except dock.MoteurIndisponible as exc:
        db.commit()  # persiste le job "refuse" pour l'audit
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(job)
    return DockingJobOut.model_validate(job)


@router.get("", response_model=list[DockingJobOut])
def lister_jobs(
    project_id: int | None = None,
    limit: int | None = Query(default=None, ge=1, le=500, description="Pagination : nombre max de résultats (défaut : tout)."),
    offset: int = Query(default=0, ge=0, description="Pagination : nombre de résultats sautés."),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[DockingJobOut]:
    """Jobs de docking du projet (isolation projet)."""
    pid = projet_effectif_ou_404(db, api_key, project_id)
    return [DockingJobOut.model_validate(j) for j in dock.list_docking_jobs(db, pid, limit=limit, offset=offset)]


@router.get("/{job_id}")
def detail_job(
    job_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Détail d'un job : paramètres, méthode, versions, résultats."""
    job = _job_ou_404(db, job_id, api_key)
    return {
        "id": job.id,
        "nom": job.nom,
        "project_id": job.project_id,
        "statut": job.statut,
        "data_level": job.data_level,
        "recepteur_hash": job.recepteur_hash,
        "recepteur_nom": job.recepteur_nom,
        "ligand_smiles": job.ligand_smiles,
        "ligand_nom": job.ligand_nom,
        "parametres": job.parametres,
        "moteur": job.moteur,
        "moteur_version": job.moteur_version,
        "versions_biblio": job.versions_biblio,
        "methode": job.methode,
        "resultats": job.resultats,
        "inputs_hash": job.inputs_hash,
        "execute_par": job.execute_par,
        "provenance": job.provenance,
    }
