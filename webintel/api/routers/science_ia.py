"""Routeur API des fonctionnalités d'IA — Phase 11 (entièrement désactivables).

- ``GET /api/science/ia/statut`` : état du garde-fou global (actif ou non,
  et d'où vient la décision).
- ``GET /api/science/ia/synthese/plante/{plant_id}`` : synthèse automatique
  d'une plante. Si l'IA est désactivée → 503 explicite (« fonctionnalité
  désactivée »), aucun calcul, aucun accès IA.

Comme les autres routeurs science : ``api_key_auth`` (+ ``get_db``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webintel.api.deps import api_key_auth, get_db
from webintel.config.runtime import is_ai_enabled
from webintel.database.models import ApiKey
from webintel.science import ia as ia_service

router = APIRouter(prefix="/api/science/ia", tags=["science"])


@router.get("/statut")
def statut_ia(
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """État du garde-fou IA : actif ou coupé, et source de la décision."""
    active, source = is_ai_enabled()
    return {
        "ia_activee": active,
        "source_decision": source,
        "message": (
            "Fonctionnalités d'IA activées."
            if active
            else "Fonctionnalités d'IA désactivées : aucune fonction d'IA "
                 "n'est exécutée ni proposée (variable WEBINTEL_AI_ENABLED "
                 "ou page « Paramètres »)."
        ),
    }


@router.get("/synthese/plante/{plant_id}")
def synthese_plante(
    plant_id: int,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> dict:
    """Synthèse automatique des connaissances sur une plante (éphémère).

    503 explicite si l'IA est désactivée — la fonction n'est alors même pas
    appelée (pas d'appel silencieux). Le résultat porte le niveau
    ``hypothese``, cite ses sources et n'est jamais persisté.
    """
    try:
        return ia_service.synthese_plante(db, plant_id)
    except ia_service.IaDesactiveeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
