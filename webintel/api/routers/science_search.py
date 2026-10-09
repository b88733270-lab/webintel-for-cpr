"""Routeur de recherche scientifique plein corpus — Phase 4 (lecture seule).

GET ``/api/science/recherche`` : une seule requête interroge les plantes,
les composés, les preuves (publications) et — uniquement si ``project_id``
est fourni — les simulations du projet (données de laboratoire cloisonnées
via ``require_project_access`` : 403 vers un projet étranger en mode
serveur).

Lecture seule : aucune écriture, aucun commit. Les erreurs de paramètres
sont des 400 avec messages en français.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from webintel.api.deps import api_key_auth, get_db, require_project_access
from webintel.science.search import search_science

router = APIRouter()

_TYPES_VALIDES = ("plante", "compose", "publication", "simulation")


@router.get("/science/recherche")
def recherche_science(
    q: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="Texte recherché (2 caractères significatifs minimum, "
        "200 maximum).",
    ),
    type: list[str] | None = Query(  # noqa: A002 — nom de paramètre imposé par l'API
        default=None,
        description="Restreint la recherche à ces groupes (répétable) : "
        "plante, compose, publication, simulation.",
    ),
    data_level: str | None = Query(
        default=None,
        description="Ne garde que les résultats de ce niveau de données "
        "(ex. publie, prediction).",
    ),
    partie: str | None = Query(
        default=None,
        description="Filtre les liens plante-composé sur la partie de plante "
        "(valeur contenue dans plant_part, insensible à la casse).",
    ),
    project_id: int | None = Query(
        default=None,
        description="Projet du laboratoire : requis pour interroger les "
        "simulations (données cloisonnées au projet).",
    ),
    limit: int = Query(
        default=20, ge=1, le=100,
        description="Résultats par groupe (1 à 100).",
    ),
    offset: int = Query(
        default=0, ge=0,
        description="Décalage de pagination par groupe (≥ 0).",
    ),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Recherche plein corpus scientifique (plantes, composés, publications,
    simulations du projet).

    - ``q`` trop court (< 2 caractères significatifs) → 400 ;
    - ``type`` / ``data_level`` inconnus → 400 ;
    - ``project_id`` d'un projet étranger (mode serveur) → 403.

    Réponse : ``{"q", "plantes", "composes", "publications", "simulations",
    "total", "limit", "offset"}``. Chaque résultat porte son ``data_level``
    et son badge ; les simulations portent ``data_level = "prediction"`` et
    un avertissement explicite (jamais présentées comme des preuves).
    """
    requete = (q or "").strip()
    if len(requete) < 2:
        raise HTTPException(
            status_code=400,
            detail=(
                "La recherche nécessite au moins 2 caractères significatifs "
                "(espaces non compris)."
            ),
        )
    if project_id is not None:
        # 403 en mode serveur si la clé n'est pas rattachée à ce projet
        # (en local : aucun contrôle, comportement inchangé).
        require_project_access(api_key, project_id)
    try:
        return search_science(
            db,
            requete,
            types=type,
            data_level=data_level,
            partie=partie,
            project_id=project_id,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc) or "Paramètres de recherche invalides.",
        ) from exc
