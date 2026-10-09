"""Routeur API du rapport de recherche exportable (v2.1).

- ``GET /api/science/rapport?format=`` : rapport structuré
  (json | html | markdown | pdf). Sections : FICHE DE LA PLANTE,
  COMPOSÉS, PUBLICATIONS, DONNÉES, HYPOTHÈSES, SIMULATIONS,
  COMBINAISONS ANALYSÉES, RÉSULTATS, LIMITES, RÉFÉRENCES.
- ``GET /api/science/rapport/zip`` : ZIP de recherche avec les données
  nécessaires à la reproductibilité (entrées, hash, versions,
  paramètres).

``inclure_prive=true`` inclut les données privées du laboratoire
(défaut : exclues — séparation labo / public).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from webintel.api.deps import (
    api_key_auth,
    get_db,
    limite_debit_couteux,
    projet_effectif_ou_404,
)
from webintel.science import rapport as rapport_module

router = APIRouter(prefix="/api/science/rapport")


@router.get("")
def get_rapport(
    format: str = Query(default="json", pattern="^(json|html|markdown|pdf)$"),
    inclure_prive: bool = Query(default=False),
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
    _limite: None = Depends(limite_debit_couteux),
):
    """Rapport de recherche du projet (format au choix)."""
    pid = projet_effectif_ou_404(db, api_key, project_id)
    try:
        rapport = rapport_module.build_rapport_recherche(
            db, pid, inclure_prive=inclure_prive
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    nom = f"rapport-recherche-{pid}"
    if format == "html":
        return Response(
            content=rapport_module.render_html(rapport),
            media_type="text/html",
        )
    if format == "markdown":
        return Response(
            content=rapport_module.render_markdown(rapport),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{nom}.md"'},
        )
    if format == "pdf":
        pdf = rapport_module.build_pdf(rapport)
        if pdf is None:
            # Honnêteté : jamais un faux PDF — on sert le HTML imprimable.
            return Response(
                content=rapport_module.render_html(rapport),
                media_type="text/html",
                headers={"X-Avertissement": "reportlab indisponible : HTML servi"},
            )
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{nom}.pdf"'},
        )
    return rapport


@router.get("/zip")
def get_zip(
    inclure_prive: bool = Query(default=False),
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
    _limite: None = Depends(limite_debit_couteux),
):
    """ZIP de recherche (rapport + donnees/entrees.json + MANIFEST)."""
    pid = projet_effectif_ou_404(db, api_key, project_id)
    try:
        contenu = rapport_module.build_zip_recherche(
            db, pid, inclure_prive=inclure_prive
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(
        content=contenu,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="dossier-recherche-{pid}.zip"'
        },
    )
