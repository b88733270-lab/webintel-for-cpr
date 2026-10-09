"""Routeur exports : construction et téléchargement des fichiers."""

from __future__ import annotations

import mimetypes
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from webintel.api import schemas
from webintel.api.deps import (
    api_key_auth,
    get_db,
    get_project_or_404,
    require_project_access,
    scoped_project_id,
)
from webintel.database.models import Export
from webintel.exports.writers import build_export
from webintel.ui.auth import api_key_or_ui_session

router = APIRouter()

_MEDIA_TYPES = {
    "json": "application/json",
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.post("/exports", response_model=schemas.ExportOut)
def create_export(
    body: schemas.ExportCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Construit un export (JSON/CSV/XLSX) et retourne son URL de téléchargement."""
    project_id = scoped_project_id(api_key, body.project_id)
    get_project_or_404(db, project_id)
    try:
        export_id, _bytes = build_export(
            db, project_id, body.kind, body.format, body.filters
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return {"id": export_id, "download_url": f"/api/exports/{export_id}/download"}


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: int,
    db: Session = Depends(get_db),
    # En mode serveur, le navigateur (depuis l'UI) ne peut pas poser
    # X-API-Key : on accepte aussi la session UI (cookie signé).
    api_key=Depends(api_key_or_ui_session),
) -> FileResponse:
    """Télécharge le fichier d'un export terminé."""
    export = db.get(Export, export_id)
    if export is None:
        raise HTTPException(status_code=404, detail=f"Export introuvable : id={export_id}.")
    # Contrôle d'accès AVANT la vérification du fichier (pas d'oracle).
    require_project_access(api_key, export.project_id)
    if export.status != "ready" or not export.file_path or not os.path.isfile(export.file_path):
        raise HTTPException(
            status_code=404,
            detail="Fichier d'export indisponible (export non terminé ou fichier manquant).",
        )
    filename = f"webintel-{export.kind}-{export.id}.{export.format}"
    return FileResponse(
        export.file_path,
        media_type=_MEDIA_TYPES.get(export.format, mimetypes.guess_type(export.file_path)[0]),
        filename=filename,
    )
