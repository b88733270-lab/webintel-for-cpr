"""Routeur API des études de combinaison (v2.1, MODE A).

- ``POST /api/science/combinaisons`` : crée une étude (plante + partie
  + composés + cible + références) → dossier d'analyse en 12 points,
  statut, hash des entrées. 422 si < 2 composés ou référence inconnue.
- ``GET /api/science/combinaisons`` : liste les études du projet.
- ``GET /api/science/combinaisons/{id}`` : détail (12 sections).
- ``GET /api/science/combinaisons/{id}/rapport?format=`` : rapport
  exportable (json | html | markdown).

Niveau de données : ``prediction`` — chaque réponse rappelle
« Résultat de screening informatique ». Aucune formulation interdite
(« efficace », « sûr »...) n'est jamais produite (auto-contrôle +
tests).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    projet_effectif_ou_404,
    require_project_access,
)
from webintel.science import combinaisons as comb
from webintel.science import versions as versions_module

router = APIRouter(prefix="/api/science/combinaisons")


def _identite(api_key) -> str:
    if api_key is None:
        return "local"
    return f"clé:{getattr(api_key, 'prefix', None) or '?'}"


class EtudeCreate(BaseModel):
    """Création d'une étude de combinaison."""

    nom: str = Field(min_length=1, max_length=255)
    compound_ids: list[int] = Field(min_length=2)
    plant_id: int | None = None
    plant_part: str | None = Field(default=None, max_length=128)
    target: str | None = None
    evidence_ids: list[int] = Field(default_factory=list)
    parametres: dict = Field(default_factory=dict)
    description: str | None = None
    project_id: int | None = None


class EtudeOut(BaseModel):
    """Étude exposée (résultat = prédiction informatique)."""

    id: int
    project_id: int
    nom: str
    description: str | None
    plant_id: int | None
    plant_part: str | None
    compound_ids: list
    target: str | None
    evidence_ids: list
    inputs_hash: str
    statut: str
    data_level: str
    avertissement: str = comb.AVERTISSEMENT_ETUDE
    model_config = {"from_attributes": True}


def _etude_ou_404(db: Session, etude_id: int, api_key):
    try:
        etude = comb.get_etude(db, etude_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    require_project_access(api_key, etude.project_id)
    return etude


@router.post("", response_model=EtudeOut, status_code=201)
def creer_etude(
    payload: EtudeCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> EtudeOut:
    """Crée une étude de combinaison et produit le dossier en 12 points."""
    project_id = projet_effectif_ou_404(db, api_key, payload.project_id)
    try:
        etude = comb.creer_etude(
            db,
            project_id=project_id,
            nom=payload.nom,
            compound_ids=payload.compound_ids,
            plant_id=payload.plant_id,
            plant_part=payload.plant_part,
            target=payload.target,
            evidence_ids=payload.evidence_ids,
            parametres=payload.parametres,
            description=payload.description,
            cree_par=_identite(api_key),
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # Versionnage (chantier PERSISTANCE + PERFORMANCE) : chaque exécution
    # crée une NOUVELLE étude ET sa version initiale (v1) — jamais
    # d'écrasement silencieux.
    versions_module.enregistrer_version(
        db, etude, cree_par=_identite(api_key), resume="Création de l'étude"
    )
    db.commit()
    db.refresh(etude)
    return EtudeOut.model_validate(etude)


@router.get("", response_model=list[EtudeOut])
def lister_etudes(
    project_id: int | None = None,
    limit: int | None = Query(default=None, ge=1, le=500, description="Pagination : nombre max de résultats (défaut : tout)."),
    offset: int = Query(default=0, ge=0, description="Pagination : nombre de résultats sautés."),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[EtudeOut]:
    """Liste les études de combinaison du projet (isolation projet)."""
    pid = projet_effectif_ou_404(db, api_key, project_id)
    return [EtudeOut.model_validate(e) for e in comb.list_etudes(db, pid, limit=limit, offset=offset)]


@router.get("/versions/{version_a}/comparer/{version_b}")
def comparer_versions_etude(
    version_a: int,
    version_b: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Compare deux versions d'une même étude (v1 vs v2).

    Sections comparées : entrées, paramètres, logiciels/versions,
    résultats (dossier en 12 points). 404 si une version est absente,
    422 si elles n'appartiennent pas à la même étude, 403 si l'étude
    appartient à un projet étranger à la clé.
    """
    try:
        va = versions_module.get_version(db, version_a)
        vb = versions_module.get_version(db, version_b)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if va.study_id != vb.study_id:
        raise HTTPException(
            status_code=422,
            detail="Comparaison impossible : ces deux versions "
                   "n'appartiennent pas à la même étude.",
        )
    try:
        etude = comb.get_etude(db, va.study_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    require_project_access(api_key, etude.project_id)
    return versions_module.comparer_versions(db, version_a, version_b)


@router.get("/{etude_id}/versions")
def lister_versions_etude(
    etude_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[dict]:
    """Historique versionné (append-only) d'une étude — 403 si projet étranger."""
    try:
        etude = comb.get_etude(db, etude_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    require_project_access(api_key, etude.project_id)
    return [versions_module._info_version(v)
            for v in versions_module.list_versions(db, etude_id)]


@router.post("/{etude_id}/versions")
def instantane_version_etude(
    etude_id: int,
    resume: str = Query(default="",
                        description="Résumé lisible de cette version."),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Enregistre un instantané de l'état actuel (nouvelle version).

    Append-only : l'étude n'est jamais modifiée, une nouvelle ligne
    ``study_versions`` est créée. 403 si projet étranger.
    """
    try:
        etude = comb.get_etude(db, etude_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    require_project_access(api_key, etude.project_id)
    version = versions_module.enregistrer_version(
        db, etude, cree_par=_identite(api_key), resume=resume or None)
    db.commit()
    return versions_module._info_version(version)


@router.get("/{etude_id}")
def detail_etude(
    etude_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Détail d'une étude : les 12 sections + métadonnées."""
    etude = _etude_ou_404(db, etude_id, api_key)
    return {
        "id": etude.id,
        "nom": etude.nom,
        "description": etude.description,
        "project_id": etude.project_id,
        "inputs_hash": etude.inputs_hash,
        "statut": etude.statut,
        "data_level": etude.data_level,
        "avertissement": comb.AVERTISSEMENT_ETUDE,
        "dossier": etude.resultats,
        "provenance": etude.provenance,
    }


@router.get("/{etude_id}/rapport")
def rapport_etude(
    etude_id: int,
    format: str = Query(default="json", pattern="^(json|html|markdown)$"),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
):
    """Rapport exportable d'une étude (json | html | markdown)."""
    etude = _etude_ou_404(db, etude_id, api_key)
    if format == "html":
        return Response(
            content=comb.rendre_html_etude(etude), media_type="text/html"
        )
    if format == "markdown":
        return Response(
            content=comb.rendre_markdown_etude(etude),
            media_type="text/markdown",
            headers={
                "Content-Disposition":
                    f'attachment; filename="etude-combinaison-{etude_id}.md"'
            },
        )
    return {
        "id": etude.id,
        "nom": etude.nom,
        "inputs_hash": etude.inputs_hash,
        "statut": etude.statut,
        "data_level": etude.data_level,
        "avertissement": comb.AVERTISSEMENT_ETUDE,
        "dossier": etude.resultats,
    }
