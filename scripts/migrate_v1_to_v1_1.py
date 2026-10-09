#!/usr/bin/env python3
"""Migration v1 -> v1.1 : observations par source (P0-3).

Contexte : en v1, `Entity.data` portait une valeur GLOBALE fusionnée
(ex. un seul prix même si le produit était vu sur 3 sites). En v1.1,
chaque valeur métier par source vit dans `source_observations`
(`SourceObservation` : entité, domaine source, champ, valeur, date).

Ce script convertit chaque entité v1 existante :
- pour chaque champ suivi de son type (`product` -> price/availability/name/brand,
  `article` -> title/author/published_at, `company` -> name/phones/emails,
  `webpage` -> title), crée UNE `SourceObservation` avec :
  - `source_url` = première source connue de l'entité (ou ""),
  - `source_domain` = domaine racine correspondant,
  - `observed_at` = `entity.last_seen`,
  - `confidence` = 1.0 ;
- idempotent : les entités possédant déjà des observations par source
  sont ignorées.

Alternative (base de test / démo) : supprimer la base SQLite
(`rm webintel.db`) et relancer l'application — `init_db()` recrée le
schéma v1.1 via `create_all`. En production avec PostgreSQL, utilisez ce
script (Alembic est prévu en v2, cf. docs/roadmap.md).

Usage :
    DATABASE_URL="sqlite:///./webintel.db" python3 scripts/migrate_v1_to_v1_1.py
"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from webintel.database.base import SessionLocal, init_db  # noqa: E402
from webintel.database.models import Entity, SourceObservation  # noqa: E402
from webintel.entities.registry import _TRACKED_FIELDS, _is_empty, _json_key  # noqa: E402
from webintel.normalization.urls import host_of  # noqa: E402


def migrate() -> dict:
    """Exécute la migration. Retourne `{entities_traitees, observations_creees}`."""
    init_db()  # crée la table source_observations si absente
    session = SessionLocal()
    entities_traitees = 0
    observations_creees = 0
    try:
        entities = session.query(Entity).order_by(Entity.id).all()
        for entity in entities:
            already = (
                session.query(SourceObservation.id)
                .filter(SourceObservation.entity_id == entity.id)
                .first()
            )
            if already is not None:
                continue  # idempotent : déjà migrée
            entities_traitees += 1
            sources = list(entity.sources or [])
            source_url = sources[0] if sources else ""
            domain = host_of(source_url)
            data = entity.data or {}
            for field in _TRACKED_FIELDS.get((entity.entity_type or "").lower(), ("title",)):
                value = data.get(field)
                if _is_empty(value):
                    continue
                session.add(SourceObservation(
                    entity_id=entity.id,
                    source_url=source_url,
                    source_domain=domain,
                    field=field,
                    value=value,
                    confidence=1.0,
                    snapshot_id=None,
                    observed_at=entity.last_seen,
                ))
                observations_creees += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return {
        "entities_traitees": entities_traitees,
        "observations_creees": observations_creees,
    }


if __name__ == "__main__":
    stats = migrate()
    print(
        f"Migration v1 -> v1.1 terminée : "
        f"{stats['entities_traitees']} entité(s) traitée(s), "
        f"{stats['observations_creees']} observation(s) par source créée(s)."
    )
