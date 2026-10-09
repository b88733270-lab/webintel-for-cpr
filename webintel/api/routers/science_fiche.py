"""Routeur API : fiche scientifique complète d'une plante médicinale.

GET ``/science/plants/{plant_id}/fiche`` — assemble la fiche construite par
``webintel.science.fiche.build_plant_fiche`` :

- partie publique (plante, botanique, pharmacognosie, phytochimie,
  pharmacologie, sécurité, bibliographie) : accessible avec le même
  pattern d'authentification que les endpoints publics de
  ``routers/science.py`` (clé API optionnelle selon
  ``REQUIRE_API_KEY``) ;
- partie laboratoire (observations, simulations) : uniquement si le
  paramètre ``project_id`` est fourni, cloisonnée au projet via
  ``require_project_access`` (403 si la clé appartient à un autre
  projet — la 403 prime sur la 404, comme dans ``routers/science.py``).

Ce routeur ne modifie pas ``routers/science.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from webintel.api.deps import api_key_auth, get_db, require_project_access
from webintel.science.fiche import build_plant_fiche

router = APIRouter()


@router.get("/science/plants/{plant_id}/fiche")
def get_plant_fiche(
    plant_id: int,
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Fiche scientifique complète d'une plante (404 si absente, message FR).

    Sans ``project_id`` : fiche publique uniquement (les sections
    laboratoire sont vides). Avec ``project_id`` : ajoute les observations
    de laboratoire et les simulations du projet — ``require_project_access``
    garantit le cloisonnement (403 si le projet est étranger à la clé).
    """
    if project_id is not None:
        require_project_access(api_key, project_id)
    try:
        return build_plant_fiche(db, plant_id, project_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Plante introuvable : id={plant_id}.",
        ) from exc
