"""Routeur de contrôle qualité des données — v2.2 (lecture seule).

GET ``/api/science/qualite`` : exécute les neuf détecteurs qualité sur
le corpus (lecture seule, aucun commit) et retourne les anomalies avec
leur sévérité, la valeur problématique et l'action recommandée. Aucune
correction automatique n'est jamais appliquée.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from webintel.api.deps import api_key_auth, get_db
from webintel.science import qualite

router = APIRouter()

_DETECTEURS_CONNUS = (
    "smiles_invalides",
    "doublons_composes",
    "doublons_publications",
    "taxons_ambigus",
    "synonymes_contradictoires",
    "relations_sans_source",
    "echantillons_sans_voucher",
    "donnees_incompletes",
    "unites_incoherentes",
)


@router.get(
    "/science/qualite",
    dependencies=[Depends(api_key_auth)],
    summary="Contrôle qualité des données (lecture seule)",
)
def controle_qualite(
    detecteur: list[str] | None = Query(
        default=None,
        description="Restreint le contrôle à ces détecteurs (répétable) : "
        + ", ".join(_DETECTEURS_CONNUS)
        + ".",
    ),
    limite: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Anomalies maximales retournées (1 à 1000).",
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Exécute le contrôle qualité (lecture seule).

    Retourne ``{date_controle, resume, anomalies, verifications_ignorees,
    total_anomalies, troncature, rappel}`` : chaque anomalie porte
    ``{categorie, severite, entite_type, entite_id, titre, detail,
    action_proposee}``.
    """
    try:
        resultat = qualite.executer_controle(db, detecteurs=detecteur)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    anomalies = resultat["anomalies"]
    return {
        "date_controle": resultat["date_controle"],
        "resume": resultat["resume"],
        "anomalies": anomalies[:limite],
        "verifications_ignorees": resultat["verifications_ignorees"],
        "total_anomalies": len(anomalies),
        "troncature": len(anomalies) > limite,
        "rappel": (
            "Contrôle en lecture seule : aucune correction automatique. "
            "Chaque anomalie propose une action — le chercheur décide."
        ),
    }
