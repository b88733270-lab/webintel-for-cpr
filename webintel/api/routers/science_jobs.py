"""Jobs asynchrones pour les calculs scientifiques lourds (chantier robustesse).

Les endpoints synchrones existants (``POST /api/science/simulations/run``,
``POST /api/science/docking/executer``) sont CONSERVÉS tels quels
(contrat inchangé). Ce routeur ajoute la variante asynchrone :

- ``POST /api/science/jobs/simulations`` → 202 + ``job_id`` ;
- ``POST /api/science/jobs/docking`` → 202 + ``job_id`` ;
- ``GET  /api/science/jobs/{job_id}`` → état parmi
  ``queued`` / ``running`` / ``paused`` / ``completed`` / ``failed`` /
  ``cancelled`` (+ ``error`` explicite si ``failed``) ;
- ``POST /api/science/jobs/{job_id}/annuler`` → demande d'annulation.

Limites honnêtes :
- l'annulation est coopérative : un docking déjà lancé dans le binaire
  Vina (appel bloquant) s'interrompt à la fin du calcul, pas au milieu ;
- les entrées invalides ou un moteur indisponible donnent un job
  ``failed`` avec un message explicite (jamais de faux résultat) ;
- le résultat reste une PRÉDICTION informatique (``data_level`` /
  avertissement conservés par les services sous-jacents).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    limite_debit_couteux,
    projet_effectif_ou_404,
    require_project_access_strict,
)
from webintel.api.routers.science_docking import DockingExecuterIn
from webintel.api.routers.science_simulations import SimulationRunRequest
from webintel.jobs.manager import JobContext, get_manager
from webintel.science import docking as dock
from webintel.simulations import registry
from webintel.simulations.service import run_simulation

router = APIRouter(prefix="/api/science/jobs", tags=["science"])


class JobSoumisOut(BaseModel):
    job_id: int
    kind: str
    status: str


class JobStatutOut(BaseModel):
    id: int
    kind: str
    status: str
    progress: dict
    error: str | None
    created_at: str | None
    updated_at: str | None


# ---------------------------------------------------------------------------
# Runners (exécutés dans le ThreadPoolExecutor du JobManager)
# ---------------------------------------------------------------------------

from concurrent.futures import CancelledError as _Cancelled


def _runner_docking(
    params: dict, contexte: JobContext, session: Session
) -> None:
    """Exécute le docking en tâche de fond (ne committe pas : le manager
    s'en charge)."""
    if contexte.is_cancelled():
        raise _Cancelled()
    contexte.update_progress(etape="validation")
    try:
        job = dock.executer_docking(
            session,
            project_id=params["project_id"],
            nom=params["nom"],
            recepteur_pdb=params["recepteur_pdb"],
            ligand_smiles=params["ligand_smiles"],
            parametres=params.get("parametres"),
            recepteur_nom=params.get("recepteur_nom"),
            ligand_nom=params.get("ligand_nom"),
            execute_par=params.get("execute_par"),
        )
    except ValueError as exc:
        # Entrées invalides OU moteur indisponible (MoteurIndisponible est
        # une ValueError) : échec explicite, aucun faux résultat.
        raise RuntimeError(str(exc)) from exc
    # P0-2 : `executer_docking` a écrit via la session du contexte (non
    # committée). On committe AVANT le dernier `update_progress`, qui
    # écrit via UNE AUTRE session : sinon auto-deadlock SQLite (la
    # transaction d'écriture ouverte bloquerait l'UPDATE du job).
    session.commit()
    if contexte.is_cancelled():
        raise _Cancelled()
    contexte.update_progress(
        etape="termine", docking_job_id=job.id, statut=job.statut
    )


def _runner_simulation(
    params: dict, contexte: JobContext, session: Session
) -> None:
    """Exécute une simulation en tâche de fond."""
    if contexte.is_cancelled():
        raise _Cancelled()
    contexte.update_progress(etape="calcul")
    try:
        simulation, _rapport = run_simulation(
            session,
            engine_name=params["engine_name"],
            name=params["name"],
            inputs=params.get("inputs") or {},
            parameters=params.get("parameters") or {},
            project_id=params["project_id"],
            description=params.get("description"),
            references=params.get("references") or [],
            executed_by=params.get("execute_par"),
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    # P0-2 : voir `_runner_docking` — committe les écritures du moteur
    # avant le `update_progress` final (autre session → sinon deadlock).
    session.commit()
    if contexte.is_cancelled():
        raise _Cancelled()
    contexte.update_progress(etape="termine", simulation_id=simulation.id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _job_ou_404(db: Session, job_id: int, api_key) -> dict:
    try:
        statut = get_manager().status(job_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Job introuvable : id={job_id}."
        ) from None
    # Cloisonnement projet (convention P0-1 du codebase : la 403 prime sur
    # la 404 pour ne pas révéler l'existence des projets étrangers —
    # même contrat que `scoped_project_id`/`require_project_access`) :
    # job inexistant → 404 ; job d'un autre projet → 403 en mode serveur.
    from webintel.database.base import SessionLocal
    from webintel.database.models import CrawlJob

    session = SessionLocal()
    try:
        job = session.get(CrawlJob, job_id)
        pid = job.project_id if job else None
    finally:
        session.close()
    if pid is None:
        raise HTTPException(
            status_code=404, detail=f"Job introuvable : id={job_id}."
        )
    require_project_access_strict(api_key, pid)
    return statut


@router.post("/docking", response_model=JobSoumisOut, status_code=202)
def soumettre_docking(
    payload: DockingExecuterIn,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
    _limite: None = Depends(limite_debit_couteux),
) -> JobSoumisOut:
    """Soumet un docking comme job asynchrone (202 + job_id).

    La validation lourde a lieu dans le worker ; un moteur indisponible
    ou des entrées invalides donnent un job ``failed`` explicite.
    """
    project_id = projet_effectif_ou_404(db, api_key, payload.project_id)
    # P0-1 : `projet_effectif_ou_404` peut avoir CRÉÉ le projet « Défaut »
    # (flush sans commit). On committe AVANT `submit()` pour deux raisons :
    # 1. le projet du job doit exister durablement (pas de rollback en fin
    #    de requête qui laisserait un job orphelin) ;
    # 2. le flush détient un verrou d'écriture SQLite sur la session
    #    requête : `JobManager.submit` écrit depuis UNE AUTRE session, ce
    #    qui auto-bloquerait la requête (deadlock même thread).
    db.commit()
    # Validation synchrone BON MARCHÉ : le récepteur/ligand vides sont
    # rejetés immédiatement (422) plutôt qu'en job failed.
    if not payload.recepteur_pdb.strip() or not payload.ligand_smiles.strip():
        raise HTTPException(
            status_code=422,
            detail="Récepteur et ligand sont obligatoires (chaînes non vides).",
        )
    params_docking = {
        "project_id": project_id,
        "nom": payload.nom,
        "recepteur_pdb": payload.recepteur_pdb,
        "ligand_smiles": payload.ligand_smiles,
        "parametres": payload.parametres,
        "recepteur_nom": payload.recepteur_nom,
        "ligand_nom": payload.ligand_nom,
        "execute_par": _identite(api_key),
    }
    job_id = get_manager().submit(
        kind="docking",
        project_id=project_id,
        params=params_docking,
        runner=lambda contexte, session: _runner_docking(
            params_docking, contexte, session
        ),
    )
    return JobSoumisOut(job_id=job_id, kind="docking", status="queued")


@router.post("/simulations", response_model=JobSoumisOut, status_code=202)
def soumettre_simulation(
    payload: SimulationRunRequest,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
    _limite: None = Depends(limite_debit_couteux),
) -> JobSoumisOut:
    """Soumet une simulation comme job asynchrone (202 + job_id)."""
    noms_connus = {m["nom"] for m in registry.list_engines()}
    if payload.engine_name not in noms_connus:
        raise HTTPException(
            status_code=404,
            detail=f"Moteur de simulation inconnu : « {payload.engine_name} ».",
        )
    project_id = projet_effectif_ou_404(db, api_key, payload.project_id)
    # Voir `soumettre_docking` : committe le projet « Défaut »
    # éventuellement auto-créé (persistance + pas d'auto-deadlock SQLite
    # avec la session d'écriture de `JobManager.submit`).
    db.commit()
    params_simulation = {
        "project_id": project_id,
        "engine_name": payload.engine_name,
        "name": payload.name,
        "inputs": payload.inputs,
        "parameters": payload.parameters,
        "description": payload.description,
        "references": payload.references,
        "execute_par": _identite(api_key),
    }
    job_id = get_manager().submit(
        kind="simulation",
        project_id=project_id,
        params=params_simulation,
        runner=lambda contexte, session: _runner_simulation(
            params_simulation, contexte, session
        ),
    )
    return JobSoumisOut(job_id=job_id, kind="simulation", status="queued")


@router.get("/{job_id}", response_model=JobStatutOut)
def statut_job(
    job_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> JobStatutOut:
    """État d'un job : queued / running / paused / completed / failed /
    cancelled (+ ``error`` si failed)."""
    return JobStatutOut(**_job_ou_404(db, job_id, api_key))


@router.post("/{job_id}/annuler", response_model=JobStatutOut)
def annuler_job(
    job_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> JobStatutOut:
    """Demande l'annulation d'un job (coopérative : effective entre deux
    étapes du calcul)."""
    _job_ou_404(db, job_id, api_key)  # 404 si inconnu ou autre projet
    try:
        get_manager().cancel(job_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Job introuvable : id={job_id}."
        ) from None
    return JobStatutOut(**_job_ou_404(db, job_id, api_key))


def _identite(api_key) -> str:
    if api_key is None:
        return "ui-locale"
    return f"cle:{getattr(api_key, 'prefix', '?')}"
