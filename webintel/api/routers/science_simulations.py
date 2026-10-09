"""Routeur API d'exécution des moteurs de simulation (Phase 5, Phase 13).

- ``GET /science/simulations/moteurs`` : fiche publique des 5 moteurs
  (nom, version, description, disponible, raison, entrées, paramètres,
  limitations) — statut réel évalué à chaque appel, jamais figé.
- ``POST /science/simulations/run`` : exécute un moteur disponible,
  persiste la ``Simulation`` et retourne le rapport à 6 sections +
  l'avertissement « Simulation — à confirmer expérimentalement ».
- ``GET /science/simulations/{id}/rapport`` : rapport reproductible
  d'une simulation persistée (``format=json|html|markdown``), avec les
  métadonnées d'audit (qui, quand, empreinte SHA-256, version moteur).
- ``POST /science/simulations/{id}/rejouer`` : relance le même moteur
  avec les mêmes entrées/paramètres → nouvelle simulation, même
  ``input_hash`` (moteur déterministe → mêmes sorties).

Journal d'audit immuable (Phase 13) : chaque exécution enregistre
``executed_by`` (``"clé:<préfixe>"`` en mode serveur, ``"local"`` en
mode local) ; les colonnes d'audit ne sont jamais modifiables
(``PATCH`` refuse → 409) et aucune route de suppression n'existe.

Authentification : ``api_key_auth`` sur chaque route (même pattern que
``routers/science.py``). ``project_id`` résolu via
``projet_effectif_ou_404`` (403 vers un projet étranger en mode serveur ;
simulation jamais orpheline, P0-1).

Erreurs (messages français) : moteur inconnu → 404 ; moteur
indisponible ou entrées invalides → 422 explicite — aucun faux calcul
n'est jamais produit.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    limite_debit_couteux,
    projet_effectif_ou_404,
    require_project_access_strict,
)
from webintel.science.models import Simulation
from webintel.science.schemas import SIMULATION_AVERTISSEMENT
from webintel.simulations import registry
from webintel.simulations.reports import (
    AVERTISSEMENT,
    build_report_from_simulation,
    render_html,
    render_markdown,
)
from webintel.simulations.service import run_simulation

router = APIRouter()


def _identite_appelant(api_key) -> str:
    """Identité enregistrée dans le journal d'audit (colonne `executed_by`).

    Mode serveur : ``"clé:<préfixe>"`` (préfixe public de la clé, jamais
    la clé en clair). Mode local (sans clé) : ``"local"``.
    """
    if api_key is None:
        return "local"
    prefixe = getattr(api_key, "prefix", None) or "?"
    return f"clé:{prefixe}"


class SimulationRunRequest(BaseModel):
    """Corps de ``POST /science/simulations/run``."""

    engine_name: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    inputs: dict = Field(default_factory=dict)
    parameters: dict = Field(default_factory=dict)
    project_id: int | None = None
    description: str | None = None
    references: list[str] = Field(default_factory=list)


class SimulationRunOut(BaseModel):
    """Réponse d'exécution : id persisté + rapport + avertissement."""

    simulation_id: int
    avertissement: str
    rapport: dict


@router.get("/science/simulations/moteurs")
def list_moteurs(api_key=Depends(api_key_auth)) -> dict:
    """Fiche des moteurs de simulation (``{"moteurs": [...]}``).

    Chaque moteur : ``nom``, ``version``, ``description``, ``disponible``
    (booléen réel), ``raison`` (pourquoi indisponible, le cas échéant),
    ``entrees``, ``parametres``, ``limitations``.
    """
    return {"moteurs": registry.list_engines()}


@router.post(
    "/science/simulations/run",
    response_model=SimulationRunOut,
    status_code=201,
)
def run_moteur(
    payload: SimulationRunRequest,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
    _limite: None = Depends(limite_debit_couteux),
) -> SimulationRunOut:
    """Exécute un moteur de simulation disponible et persiste le résultat.

    - moteur inconnu → 404 ;
    - moteur indisponible (ex. ``docking_adapter`` sans AutoDock Vina) ou
      entrées/paramètres invalides → 422 explicite, sans aucun calcul ;
    - ``project_id`` : P0-1 — résolu via ``projet_effectif_ou_404``
      (projet de la clé, 403 vers un projet étranger, en mode serveur ;
      projet « Défaut » en mode local) : la simulation créée n'est
      JAMAIS orpheline ;
    - la réponse inclut toujours ``avertissement`` : une simulation
      n'est PAS une preuve expérimentale.
    """
    # Existence du moteur : via les noms du registre (get_engine lève
    # ValueError pour un moteur connu mais indisponible — cas géré plus
    # bas en 422, pas en 404).
    noms_connus = {m["nom"] for m in registry.list_engines()}
    if payload.engine_name not in noms_connus:
        raise HTTPException(
            status_code=404,
            detail=f"Moteur de simulation inconnu : « {payload.engine_name} ».",
        )
    project_id = projet_effectif_ou_404(db, api_key, payload.project_id)
    try:
        simulation, rapport = run_simulation(
            db,
            engine_name=payload.engine_name,
            name=payload.name,
            inputs=payload.inputs,
            parameters=payload.parameters,
            project_id=project_id,
            description=payload.description,
            references=payload.references,
            executed_by=_identite_appelant(api_key),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()  # le service ne committe jamais : c'est au routeur de le faire
    db.refresh(simulation)
    return SimulationRunOut(
        simulation_id=simulation.id,
        avertissement=SIMULATION_AVERTISSEMENT,
        rapport=rapport,
    )


def _simulation_ou_404(db: Session, simulation_id: int, api_key) -> Simulation:
    """Charge une simulation en appliquant le cloisonnement projet.

    P0-1 : ``require_project_access_strict`` — contrôle d'accès sur TOUTES
    les lectures, y compris une éventuelle simulation orpheline antérieure
    au durcissement (403 en mode serveur, jamais de fuite inter-projets).
    """
    simulation = db.get(Simulation, simulation_id)
    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail=f"Simulation introuvable : id={simulation_id}.",
        )
    require_project_access_strict(api_key, simulation.project_id)
    return simulation


@router.get("/science/simulations/{simulation_id}/rapport")
def rapport_simulation(
    simulation_id: int,
    format: str = Query(default="json", pattern="^(json|html|markdown)$"),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
):
    """Rapport reproductible d'une simulation persistée (Phase 13).

    Reconstruit depuis les champs persistés : INPUTS → METHOD →
    PARAMETERS → OUTPUTS → REFERENCES → LIMITATIONS, avec les
    métadonnées d'audit (``input_hash`` SHA-256, ``executed_by``,
    ``run_at``, version du moteur) et l'avertissement
    « Simulation — à confirmer expérimentalement ».

    ``format=json`` : le rapport en JSON ; ``format=html`` : page HTML
    imprimable ; ``format=markdown`` : document Markdown téléchargeable.
    """
    simulation = _simulation_ou_404(db, simulation_id, api_key)
    rapport = build_report_from_simulation(simulation)
    if format == "html":
        return Response(content=render_html(rapport), media_type="text/html")
    if format == "markdown":
        nom = f"rapport-simulation-{simulation_id}.md"
        return Response(
            content=render_markdown(rapport),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{nom}"'},
        )
    return rapport


class SimulationRejouerOut(BaseModel):
    """Réponse du rejeu : nouvelle simulation + avertissement."""

    simulation_id: int
    input_hash: str
    avertissement: str = AVERTISSEMENT


@router.post(
    "/science/simulations/{simulation_id}/rejouer",
    response_model=SimulationRejouerOut,
    status_code=201,
)
def rejouer_simulation(
    simulation_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> SimulationRejouerOut:
    """Rejoue une simulation persistée avec les mêmes entrées (Phase 13).

    Relance ``engine_name`` avec les ``inputs`` / ``parameters``
    persistés : une NOUVELLE simulation est créée (journal append-only,
    l'originale n'est jamais modifiée). Mêmes entrées → même
    ``input_hash`` ; pour un moteur déterministe, mêmes sorties.

    422 explicite si le moteur est indisponible ou si la simulation
    d'origine n'a pas d'entrées rejouables (création manuelle sans
    moteur).
    """
    simulation = _simulation_ou_404(db, simulation_id, api_key)
    if not simulation.engine_name:
        raise HTTPException(
            status_code=422,
            detail="Rejeu impossible : cette simulation n'a pas été "
            "produite par un moteur (création manuelle, sans entrées "
            "rejouables).",
        )
    if not simulation.inputs:
        raise HTTPException(
            status_code=422,
            detail="Rejeu impossible : aucune entrée persistée pour "
            "cette simulation (donnée antérieure à la Phase 13).",
        )
    try:
        nouvelle, _rapport = run_simulation(
            db,
            engine_name=simulation.engine_name,
            name=f"{simulation.name} (rejeu)",
            inputs=dict(simulation.inputs),
            parameters=dict(simulation.parameters or {}),
            project_id=simulation.project_id,
            description=simulation.description,
            references=list(simulation.references or []),
            executed_by=_identite_appelant(api_key),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(nouvelle)
    return SimulationRejouerOut(
        simulation_id=nouvelle.id,
        input_hash=nouvelle.input_hash,
        avertissement=AVERTISSEMENT,
    )
