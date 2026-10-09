"""Routeur API : connecteurs de sources externes — Phase 10.

- ``GET /api/science/connecteurs`` : liste les connecteurs déclarés
  (contrat pour la page UI : clés exactes
  ``nom, description, url, licence, licence_url, conditions_utilisation,
  version_connecteur, statut, dernier_import, strategie_maj``).
- ``POST /api/science/connecteurs/{nom}/importer`` : déclenche un import
  sélectif depuis la source (lecture seule, avec provenance).

Authentification : ``api_key_auth`` sur chaque route (comme les autres
routeurs science). Importer est une écriture locale (objets + provenance)
à partir d'une source distante en lecture seule.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from webintel.api.deps import api_key_auth, get_db
from webintel.core.utils import utcnow
from webintel.science.connecteurs.base import (
    ConnecteurIndisponible,
    ParametresInvalides,
)
from webintel.science.connecteurs import registry
from webintel.science.models import SourceConnecteur

router = APIRouter()

AVERTISSEMENT_IMPORT = (
    "Données importées d'une source externe : niveau NORMALISE — "
    "ce ne sont ni des observations expérimentales ni des données "
    "« scientifiquement validées ». Provenance enregistrée "
    "(connecteur, date, version, licence)."
)


class ImportBody(BaseModel):
    """Corps de la demande d'import."""

    parametres: dict[str, Any] = Field(default_factory=dict)
    projet_id: int | None = None


def _fiche_connecteur(ligne: SourceConnecteur) -> dict[str, Any]:
    """Sérialise une ligne SourceConnecteur (clés du contrat UI)."""
    return {
        "nom": ligne.nom,
        "description": ligne.description,
        "url": ligne.url,
        "licence": ligne.licence,
        "licence_url": ligne.licence_url,
        "conditions_utilisation": ligne.conditions_utilisation,
        "version_connecteur": ligne.version_connecteur,
        "statut": ligne.statut,
        "dernier_import": (
            ligne.dernier_import.isoformat()
            if ligne.dernier_import is not None
            else None
        ),
        "strategie_maj": ligne.strategie_maj,
    }


@router.get("", response_model=None)
def lister(
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict[str, Any]:
    """Liste les connecteurs de sources externes déclarés.

    Lecture seule, SANS effet de bord : l'amorçage des lignes
    ``SourceConnecteur`` a lieu au démarrage (``init_db`` — correctif
    P2-1), jamais dans ce GET.
    """
    lignes = (
        db.query(SourceConnecteur)
        .order_by(SourceConnecteur.nom)
        .all()
    )
    return {"connecteurs": [_fiche_connecteur(l) for l in lignes]}


@router.post("/{nom}/importer", response_model=None)
def importer(
    nom: str,
    body: ImportBody,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict[str, Any]:
    """Importe sélectivement depuis le connecteur ``nom``.

    - connecteur inconnu → 404 ;
    - paramètres invalides → 422 ;
    - source injoignable → 503 explicite (aucune donnée importée).
    """
    try:
        connecteur = registry.get_connecteur(nom)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    ligne = (
        db.query(SourceConnecteur)
        .filter(SourceConnecteur.nom == nom)
        .one_or_none()
    )
    if ligne is None:
        registry.initialiser_connecteurs(db)
        db.flush()
        ligne = (
            db.query(SourceConnecteur)
            .filter(SourceConnecteur.nom == nom)
            .one()
        )
    if ligne.statut != "actif":
        raise HTTPException(
            status_code=409,
            detail=f"Connecteur « {nom} » inactif : import refusé.",
        )

    try:
        rapport = connecteur.importer(db, body.parametres or {})
    except ParametresInvalides as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except ConnecteurIndisponible as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc))

    ligne.dernier_import = utcnow()
    db.commit()

    reponse: dict[str, Any] = {
        "rapport": rapport,
        "provenance": rapport.get("provenance", {}),
        "avertissement": AVERTISSEMENT_IMPORT,
    }
    if body.projet_id is not None:
        reponse["projet_id"] = body.projet_id
    return reponse
