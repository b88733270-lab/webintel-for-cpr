#!/usr/bin/env python3
"""Bootstrap production (P0-1) : crée un projet et/ou une clé API.

En mode `REQUIRE_API_KEY=true`, l'API ne permet pas de créer un projet
avec une clé (une clé est strictement rattachée à UN projet existant) :
ce script est la voie prévue pour l'initialisation.

Usage :
    python3 scripts/create_api_key.py --project "Veille prix" [--name "clé prod"]

Affiche la clé EN CLAIR une seule fois : copiez-la aussitôt
(seul son sha256 est stocké en base).
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from webintel.core.utils import sha256_hex  # noqa: E402
from webintel.database.base import SessionLocal, init_db  # noqa: E402
from webintel.database.models import ApiKey, Project  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Crée un projet et une clé API WebIntel."
    )
    parser.add_argument("--project", required=True, help="Nom du projet")
    parser.add_argument("--name", default="clé principale",
                        help="Nom de la clé API (défaut : 'clé principale')")
    args = parser.parse_args()

    init_db()
    session = SessionLocal()
    try:
        project = (
            session.query(Project)
            .filter(Project.name == args.project.strip())
            .one_or_none()
        )
        if project is None:
            project = Project(name=args.project.strip())
            session.add(project)
            session.flush()
            print(f"Projet créé : id={project.id}, nom={project.name!r}")
        else:
            print(f"Projet existant : id={project.id}, nom={project.name!r}")

        plain_key = secrets.token_urlsafe(32)
        row = ApiKey(
            project_id=project.id,
            name=args.name.strip(),
            key_hash=sha256_hex(plain_key),
            prefix=plain_key[:8],
            quotas={},
            revoked=False,
        )
        session.add(row)
        session.commit()
    finally:
        session.close()

    print(f"Clé API créée : id={row.id}, projet={project.id}, préfixe={row.prefix}")
    print()
    print("=== COPIEZ CETTE CLÉ MAINTENANT (affichée une seule fois) ===")
    print(plain_key)
    print("=== En-tête à utiliser : X-API-Key: <clé> ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
