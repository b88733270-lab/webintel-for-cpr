"""Routeur projets & clés API.

Isolation (P0-1) : en mode clé API (`REQUIRE_API_KEY=true`), chaque clé est
rattachée à UN projet et ne peut accéder qu'à lui (`require_project_access`).
La création de projets est alors réservée au bootstrap (script
`scripts/create_api_key.py`) : une clé ne peut pas créer de projet auquel
elle n'aurait pas accès.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from webintel.api import schemas
from webintel.api.deps import (
    api_key_auth,
    get_db,
    get_project_or_404,
    require_project_access,
)
from webintel.core.utils import sha256_hex
from webintel.database.models import ApiKey, Project

router = APIRouter()


@router.post("/projects", response_model=schemas.ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: schemas.ProjectCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> Project:
    """Crée un projet (nom unique).

    En mode clé API : 403 — la création de projet est une opération de
    bootstrap (voir `scripts/create_api_key.py`), une clé restant
    strictement rattachée à son projet.
    """
    if api_key is not None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Création de projet interdite avec une clé API : utilisez le "
                "script de bootstrap `scripts/create_api_key.py`."
            ),
        )
    existing = db.query(Project).filter(Project.name == body.name.strip()).one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Un projet nommé « {body.name.strip()} » existe déjà.",
        )
    project = Project(name=body.name.strip())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects", response_model=list[schemas.ProjectOut])
def list_projects(
    db: Session = Depends(get_db), api_key=Depends(api_key_auth)
) -> list[Project]:
    """Liste les projets (en mode clé : uniquement celui de la clé)."""
    query = db.query(Project).order_by(Project.id)
    if api_key is not None:
        query = query.filter(Project.id == api_key.project_id)
    return query.all()


@router.get("/projects/{project_id}", response_model=schemas.ProjectOut)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> Project:
    """Détail d'un projet (403 si la clé appartient à un autre projet)."""
    require_project_access(api_key, project_id)
    return get_project_or_404(db, project_id)


@router.post("/projects/{project_id}/api-keys", response_model=schemas.ApiKeyCreatedOut)
def create_api_key(
    project_id: int,
    body: schemas.ApiKeyCreate,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> dict:
    """Crée une clé API pour un projet.

    La clé en clair (`plain_key`) n'est retournée QUE dans cette réponse :
    elle n'est jamais stockée (seul son sha256 est conservé).
    """
    require_project_access(api_key, project_id)
    get_project_or_404(db, project_id)
    plain_key = secrets.token_urlsafe(32)
    row = ApiKey(
        project_id=project_id,
        name=body.name.strip(),
        key_hash=sha256_hex(plain_key),
        prefix=plain_key[:8],
        quotas=dict(body.quotas or {}),
        revoked=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "plain_key": plain_key,
        "message": "Copiez cette clé maintenant : elle ne sera plus jamais affichée.",
    }


@router.get("/projects/{project_id}/api-keys", response_model=list[schemas.ApiKeyOut])
def list_api_keys(
    project_id: int,
    db: Session = Depends(get_db),
    api_key=Depends(api_key_auth),
) -> list[ApiKey]:
    """Liste les clés API d'un projet (sans jamais exposer les clés en clair)."""
    require_project_access(api_key, project_id)
    get_project_or_404(db, project_id)
    return (
        db.query(ApiKey)
        .filter(ApiKey.project_id == project_id)
        .order_by(ApiKey.id)
        .all()
    )
