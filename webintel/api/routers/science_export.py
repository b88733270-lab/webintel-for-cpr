"""Routeur API d'export scientifique (Phase 9).

Préfixe : ``/api/science/export`` (monté par le coordinateur dans
``webintel.main``).

Endpoints :

- ``GET /plantes?format=csv|json`` : export des plantes (téléchargement) ;
- ``GET /composes?format=csv|json`` : export des composés ;
- ``GET /evidences?format=csv|json`` : export des preuves documentaires ;
- ``GET /fiche/{plant_id}?format=pdf|html`` : fiche plante — vrai PDF via
  ``reportlab`` (``format=pdf``), HTML imprimable (``format=html``). Si
  ``reportlab`` est indisponible, ``format=pdf`` sert le HTML imprimable
  avec un bandeau explicite : on ne prétend JAMAIS servir un PDF quand
  c'est du HTML ;
- ``POST /dossier`` : dossier de recherche complet (ZIP) —
  ``{"projet_id", "inclure_prive": false, "inclure_simulations": true}``.
  Avec ``inclure_prive=false`` (défaut), les observations, notes et
  simulations privées sont exclues ; ``require_project_access`` est exigé
  pour TOUT export de dossier, quel que soit ``inclure_prive`` (P1-1 :
  403 avant 404).

Chaque ligne/objet exporté porte ``data_level`` (niveau de preuve) et
``source_principale`` (DOI ou URL). Authentification comme les autres
routeurs science : ``api_key_auth`` (+ ``get_db``). Format invalide →
400 (message FR), plante/projet absent → 404 (message FR).
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    get_project_or_404,
    limite_debit_couteux,
    require_project_access,
)
from webintel.database.models import ApiKey
from webintel.science import export_dossier
from webintel.science.export_dossier import (
    _COLONNES_COMPOSES,
    _COLONNES_EVIDENCES,
    _COLONNES_PLANTES,
)

router = APIRouter(prefix="/api/science/export")


def _format_ou_400(valeur: str, autorises: tuple[str, ...]) -> str:
    """Valide un paramètre ``format`` (400 FR si invalide)."""
    fmt = (valeur or "").strip().lower()
    if fmt not in autorises:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Format invalide : « {valeur} ». "
                f"Formats acceptés : {', '.join(autorises)}."
            ),
        )
    return fmt


def _nom_fichier(base: str, extension: str) -> str:
    """Nom de fichier de téléchargement daté (``base_aaaammjj.ext``)."""
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{base}_{date}.{extension}"


def _telechargement(
    donnees: bytes | str, nom: str, type_mime: str
) -> StreamingResponse:
    """Réponse de téléchargement de fichier (nom FR, UTF-8)."""
    corps = donnees.encode("utf-8") if isinstance(donnees, str) else donnees
    return StreamingResponse(
        io.BytesIO(corps),
        media_type=type_mime,
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )


# ---------------------------------------------------------------------------
# Exports CSV / JSON (données publiques de référence)
# ---------------------------------------------------------------------------

@router.get("/plantes")
def exporter_plantes(
    format: str = Query(default="csv", description="csv ou json"),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> StreamingResponse:
    """Export des plantes : CSV ou JSON en téléchargement.

    Chaque ligne porte ``data_level`` (``"publie"``) et
    ``source_principale`` (DOI ou publication du premier lien
    plante-composé, vide si aucun).
    """
    fmt = _format_ou_400(format, ("csv", "json"))
    lignes = export_dossier.lignes_plantes(db)
    if fmt == "csv":
        return _telechargement(
            export_dossier.export_csv(lignes, _COLONNES_PLANTES),
            _nom_fichier("webintel_plantes", "csv"),
            "text/csv; charset=utf-8",
        )
    import json

    return _telechargement(
        json.dumps(lignes, ensure_ascii=False, indent=2),
        _nom_fichier("webintel_plantes", "json"),
        "application/json; charset=utf-8",
    )


@router.get("/composes")
def exporter_composes(
    format: str = Query(default="csv", description="csv ou json"),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> StreamingResponse:
    """Export des composés : CSV ou JSON en téléchargement."""
    fmt = _format_ou_400(format, ("csv", "json"))
    lignes = export_dossier.lignes_composes(db)
    if fmt == "csv":
        return _telechargement(
            export_dossier.export_csv(lignes, _COLONNES_COMPOSES),
            _nom_fichier("webintel_composes", "csv"),
            "text/csv; charset=utf-8",
        )
    import json

    return _telechargement(
        json.dumps(lignes, ensure_ascii=False, indent=2),
        _nom_fichier("webintel_composes", "json"),
        "application/json; charset=utf-8",
    )


@router.get("/evidences")
def exporter_evidences(
    format: str = Query(default="csv", description="csv ou json"),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> StreamingResponse:
    """Export des preuves documentaires : CSV ou JSON en téléchargement."""
    fmt = _format_ou_400(format, ("csv", "json"))
    lignes = export_dossier.lignes_evidences(db)
    if fmt == "csv":
        return _telechargement(
            export_dossier.export_csv(lignes, _COLONNES_EVIDENCES),
            _nom_fichier("webintel_evidences", "csv"),
            "text/csv; charset=utf-8",
        )
    import json

    return _telechargement(
        json.dumps(lignes, ensure_ascii=False, indent=2),
        _nom_fichier("webintel_evidences", "json"),
        "application/json; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# Fiche plante : PDF réel (reportlab) ou HTML imprimable
# ---------------------------------------------------------------------------

@router.get("/fiche/{plant_id}")
def exporter_fiche(
    plant_id: int,
    format: str = Query(default="pdf", description="pdf ou html"),
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
) -> StreamingResponse:
    """Fiche plante complète en téléchargement.

    - ``format=pdf`` : VRAI PDF (``reportlab``) — sections, tableaux,
      badges en texte, sources, avertissements ;
    - ``format=html`` : HTML imprimable (CSS print).

    Si ``reportlab`` est indisponible, ``format=pdf`` sert le HTML
    imprimable avec un bandeau « PDF indisponible » : on ne prétend
    jamais servir un PDF quand c'est du HTML. 404 (FR) si la plante est
    absente, 400 (FR) si le format est invalide.
    """
    fmt = _format_ou_400(format, ("pdf", "html"))
    try:
        contenu, type_mime, nom = export_dossier.exporter_fiche(
            db, plant_id, format=fmt
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Plante introuvable : id={plant_id}."
        ) from exc
    return _telechargement(contenu, nom, type_mime)


# ---------------------------------------------------------------------------
# Dossier de recherche (ZIP)
# ---------------------------------------------------------------------------

class DossierRequest(BaseModel):
    """Corps de ``POST /dossier``."""

    projet_id: int = Field(description="Projet du dossier de recherche")
    inclure_prive: bool = Field(
        default=False,
        description=(
            "Inclure les données privées du laboratoire (observations, "
            "notes, simulations non publiées). Exige l'accès au projet."
        ),
    )
    inclure_simulations: bool = Field(
        default=True,
        description="Inclure les rapports de simulation (avec avertissement).",
    )


@router.post("/dossier")
def exporter_dossier(
    payload: DossierRequest,
    db: Session = Depends(get_db),
    api_key: ApiKey | None = Depends(api_key_auth),
    _limite: None = Depends(limite_debit_couteux),
) -> StreamingResponse:
    """Dossier de recherche complet d'un projet (ZIP en téléchargement).

    Contient : fiches JSON+HTML par plante du projet, bibliographie
    (texte + BibTeX), rapports de simulation (avec l'avertissement
    « Simulation — à confirmer expérimentalement »), journal du projet,
    MANIFEST.json et README.txt.

    P1-1 : l'accès au projet est exigé pour TOUT export de dossier,
    que ``inclure_prive`` soit vrai ou faux — la 403 prime sur la 404
    (ne pas révéler l'existence des projets étrangers).
    """
    # 403 avant 404 : ne pas révéler l'existence des projets étrangers.
    require_project_access(api_key, payload.projet_id)
    projet = get_project_or_404(db, payload.projet_id)
    archive = export_dossier.build_dossier_zip(
        db,
        projet.id,
        inclure_prive=payload.inclure_prive,
        inclure_simulations=payload.inclure_simulations,
    )
    return _telechargement(
        archive,
        _nom_fichier(f"dossier_recherche_projet_{projet.id}", "zip"),
        "application/zip",
    )
