"""Routeur API des analyses de synergie (v2.1, MODE B).

- ``GET /api/science/synergie/methodes`` : fiches des 4 méthodes
  (formule, données requises, référence).
- ``POST /api/science/synergie/analyser`` : exécute Bliss / Loewe /
  HSA / ZIP sur des DONNÉES EXPÉRIMENTALES fournies → analyse
  persistée (méthode, données utilisées, calcul, limites, graphe SVG,
  interprétation prudente).
- ``GET /api/science/synergie`` : liste les analyses du projet
  (privées exclues par défaut — séparation labo / public).
- ``GET /api/science/synergie/{id}`` : détail.

GARDE-FOU : sans les données expérimentales requises par la méthode,
l'analyse est REFUSÉE (422 « données insuffisantes pour la méthode
X ») — jamais de score inventé, jamais de synergie calculée depuis de
simples structures moléculaires.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    projet_effectif_ou_404,
    require_project_access,
)
from webintel.science import combinaisons as comb
from webintel.science.synergie import METHODES, DonneesInsuffisantes
from webintel.science.synergie import methodes_disponibles

router = APIRouter(prefix="/api/science/synergie")


def _identite(api_key) -> str:
    if api_key is None:
        return "local"
    return f"clé:{getattr(api_key, 'prefix', None) or '?'}"


class SynergieCreate(BaseModel):
    """Lancement d'une analyse de synergie sur données expérimentales."""

    nom: str = Field(min_length=1, max_length=255)
    methode: str = Field(min_length=1, max_length=16)
    donnees: dict = Field(default_factory=dict)
    parametres: dict = Field(default_factory=dict)
    is_private: bool = True
    project_id: int | None = None


class SynergieOut(BaseModel):
    """Analyse exposée (résultat = analyse informatique, pas une preuve)."""

    id: int
    project_id: int
    nom: str
    methode: str
    inputs_hash: str
    statut: str
    data_level: str
    is_private: bool
    model_config = {"from_attributes": True}


def _analyse_ou_404(db: Session, analyse_id: int, api_key):
    try:
        analyse = comb.get_analyse_synergie(db, analyse_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    require_project_access(api_key, analyse.project_id)
    return analyse


@router.get("/methodes")
def lister_methodes(api_key=Depends(api_key_auth)) -> dict:
    """Fiches des 4 méthodes de synergie (formules documentées)."""
    return {"methodes": methodes_disponibles()}


@router.post("/analyser", response_model=SynergieOut, status_code=201)
def analyser(
    payload: SynergieCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> SynergieOut:
    """Exécute une méthode de synergie sur des données expérimentales.

    - méthode inconnue → 404 ;
    - données insuffisantes → 422 explicite (« données insuffisantes
      pour la méthode X »), SANS persistance d'un faux résultat.
    """
    nom = (payload.methode or "").strip().lower()
    if nom not in METHODES:
        raise HTTPException(
            status_code=404,
            detail=f"Méthode de synergie inconnue : « {payload.methode} ». "
                   f"Méthodes disponibles : {', '.join(sorted(METHODES))}.",
        )
    project_id = projet_effectif_ou_404(db, api_key, payload.project_id)
    try:
        analyse = comb.creer_analyse_synergie(
            db,
            project_id=project_id,
            nom=payload.nom,
            methode=nom,
            donnees=payload.donnees,
            parametres=payload.parametres,
            is_private=payload.is_private,
            cree_par=_identite(api_key),
        )
    except DonneesInsuffisantes as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(analyse)
    return SynergieOut.model_validate(analyse)


@router.get("", response_model=list[SynergieOut])
def lister_analyses(
    project_id: int | None = None,
    inclure_prive: bool = Query(default=False),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[SynergieOut]:
    """Liste les analyses du projet (privées exclues par défaut)."""
    pid = projet_effectif_ou_404(db, api_key, project_id)
    analyses = comb.list_analyses_synergie(db, pid, inclure_prive=inclure_prive)
    return [SynergieOut.model_validate(a) for a in analyses]


@router.get("/{analyse_id}")
def detail_analyse(
    analyse_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Détail : méthode, données utilisées, calcul, limites, graphe, interprétation."""
    analyse = _analyse_ou_404(db, analyse_id, api_key)
    return {
        "id": analyse.id,
        "nom": analyse.nom,
        "methode": analyse.methode,
        "project_id": analyse.project_id,
        "inputs_hash": analyse.inputs_hash,
        "statut": analyse.statut,
        "data_level": analyse.data_level,
        "is_private": analyse.is_private,
        "donnees": analyse.donnees,
        "resultat": analyse.resultats,
        "provenance": analyse.provenance,
    }
