"""Écrans « intelligence Web » v3 : Sources, Comparaison, Recherche Web.

Routes UI additives (aucune route existante modifiée) :

- ``GET /sources`` : registre des sources d'observation du projet
  (hôte canonique, nombre d'observations par source et de changements,
  dernière observation) — la provenance Entity → Source → Observation
  rendue visible.
- ``GET /comparaison?entite_a=<id>&entite_b=<id>`` : deux entités du
  projet côte à côte (identité, champs observés par source, sources,
  première/dernière vue). Aucune valeur inventée : seuls les champs
  réellement observés sont affichés.
- ``GET /recherche-web?q=...&langue=fr|en&limite=n`` : recherche Web
  généraliste via l'abstraction Providers (fallback automatique).
  Online-first : sans réseau, l'écran affiche les échecs déclarés des
  providers et un bouton Réessayer — jamais de faux résultats.

Isolation : tout est borné au projet de la session (`_project_for`),
comme les autres routes UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from webintel.api.deps import get_db
from webintel.database.models import (
    ApiKey,
    Entity,
    Observation,
    Source,
    SourceObservation,
)
from webintel.search.web_service import rechercher_web
from webintel.ui.auth import ui_auth
from webintel.ui.routes import _project_for, _render

router = APIRouter()


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@router.get("/sources", response_class=HTMLResponse)
def sources_page(db: Session = Depends(get_db),
                 ui_key: ApiKey | None = Depends(ui_auth)):
    """Registre des sources : chaque donnée doit pouvoir être reliée à
    la source qui l'a produite."""
    _project_for(db, ui_key)  # garantit le projet (mode local)
    lignes = []
    for source in db.query(Source).order_by(Source.canonical_host).all():
        nb_obs = (
            db.query(func.count(SourceObservation.id))
            .filter(SourceObservation.source_id == source.id)
            .scalar()
            or 0
        )
        nb_changements = (
            db.query(func.count(Observation.id))
            .filter(Observation.source_id == source.id)
            .scalar()
            or 0
        )
        derniere = (
            db.query(func.max(SourceObservation.observed_at))
            .filter(SourceObservation.source_id == source.id)
            .scalar()
        )
        lignes.append({
            "nom": source.name,
            "hote": source.canonical_host,
            "pays": source.country,
            "vendeur": source.seller,
            "observations": nb_obs,
            "changements": nb_changements,
            "derniere_observation": derniere,
            "creee_le": source.created_at,
        })
    return _render("sources.html", ui_key=ui_key, active="sources",
                   sources=lignes, count=len(lignes))


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------


def _carte_entite(db: Session, projet_id: int,
                  entite: Entity | None) -> dict | None:
    """Vue affichable d'une entité : identité + observations par source."""
    if entite is None or entite.project_id != projet_id:
        return None
    observations = (
        db.query(SourceObservation)
        .filter(SourceObservation.entity_id == entite.id)
        .order_by(SourceObservation.source_domain,
                  SourceObservation.field,
                  SourceObservation.observed_at.desc())
        .all()
    )
    par_source: dict[str, list[dict]] = {}
    for obs in observations:
        par_source.setdefault(obs.source_domain or "(sans domaine)", []).append({
            "champ": obs.field,
            "valeur": obs.value,
            "url": obs.source_url,
            "observe_le": obs.observed_at,
        })
    return {
        "id": entite.id,
        "nom": entite.name,
        "type": entite.entity_type,
        "donnees": entite.data or {},
        "sources": entite.sources or [],
        "premiere_vue": entite.first_seen,
        "derniere_vue": entite.last_seen,
        "par_source": par_source,
    }


@router.get("/comparaison", response_class=HTMLResponse)
def comparaison_page(request: Request, db: Session = Depends(get_db),
                     ui_key: ApiKey | None = Depends(ui_auth)):
    project = _project_for(db, ui_key)
    entites = (
        db.query(Entity)
        .filter(Entity.project_id == project.id)
        .order_by(Entity.name)
        .all()
    )

    def _id_param(nom: str) -> int | None:
        brut = (request.query_params.get(nom) or "").strip()
        return int(brut) if brut.isdigit() else None

    id_a, id_b = _id_param("entite_a"), _id_param("entite_b")
    carte_a = _carte_entite(db, project.id,
                            db.get(Entity, id_a) if id_a else None)
    carte_b = _carte_entite(db, project.id,
                            db.get(Entity, id_b) if id_b else None)
    champs_a = set((carte_a or {}).get("donnees") or {})
    champs_b = set((carte_b or {}).get("donnees") or {})
    return _render(
        "comparaison.html", ui_key=ui_key, active="comparaison",
        project=project, entites=entites,
        entite_a=carte_a, entite_b=carte_b,
        id_a=id_a, id_b=id_b,
        champs_communs=sorted(champs_a & champs_b),
        champs_a_seuls=sorted(champs_a - champs_b),
        champs_b_seuls=sorted(champs_b - champs_a),
    )


# ---------------------------------------------------------------------------
# Recherche Web (providers, online-first)
# ---------------------------------------------------------------------------


@router.get("/recherche-web", response_class=HTMLResponse)
def recherche_web_page(request: Request,
                       ui_key: ApiKey | None = Depends(ui_auth)):
    """Recherche Web généraliste : providers avec fallback automatique.

    Aucun appel réseau sans requête. En cas d'échec de tous les
    providers, l'écran l'affiche explicitement (état erreur + Réessayer).
    """
    params = request.query_params
    q = (params.get("q") or "").strip()
    langue = params.get("langue") if params.get("langue") in ("fr", "en") else "fr"
    try:
        limite = min(max(int(params.get("limite") or 10), 1), 50)
    except ValueError:
        limite = 10
    resultat = None
    if q:
        resultat = rechercher_web(q, limite=limite, langue=langue)
    return _render("recherche_web.html", ui_key=ui_key, active="recherche-web",
                   q=q, langue=langue, limite=limite, resultat=resultat)
