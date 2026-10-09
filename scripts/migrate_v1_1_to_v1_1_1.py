#!/usr/bin/env python3
"""Migration v1.1 -> v1.1.1 : table `Source` + discipline des observations.

Crée (idempotent — réexécutable sans effet) :
1. la table `sources` (`Base.metadata.create_all`, checkfirst) ;
2. les colonnes manquantes sur les tables existantes :
   - `source_observations` : `source_id`, `value_hash` ;
   - `observations` (journal des changements) : `source_id`, `source_url`,
     `source_domain` ;
3. une `Source` par hôte déjà observé (`source_domain` distinct non vide) ;
4. le rattachement `source_observations.source_id` + le calcul de
   `value_hash = sha256(valeur normalisée)` pour les lignes existantes ;
5. la provenance du journal `observations` via `snapshot_id -> url` ;
6. la déduplication préalable puis l'index unique
   `(entity_id, source_id, field, value_hash)` (contrainte du modèle sur les
   bases neuves, index unique `IF NOT EXISTS` ici).

SQLite : `ALTER TABLE ... ADD COLUMN` (+ reconstruction non nécessaire),
index unique via `CREATE UNIQUE INDEX IF NOT EXISTS`.
PostgreSQL : `ADD COLUMN IF NOT EXISTS`, `CREATE UNIQUE INDEX IF NOT EXISTS`.

Usage :
    python3 scripts/migrate_v1_1_to_v1_1_1.py [--database-url URL]

Sans `--database-url`, utilise `DATABASE_URL` ou le SQLite de développement.
"""

from __future__ import annotations

import argparse
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from webintel.database.base import Base  # noqa: E402
from webintel.database.helpers import value_hash_for  # noqa: E402


def _dialect(engine) -> str:
    return engine.dialect.name


def _existing_columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _add_column(engine, table: str, column_ddl: str) -> None:
    """Ajoute une colonne si absente (SQLite et PostgreSQL)."""
    col_name = column_ddl.split()[0].strip('"')
    if col_name in _existing_columns(engine, table):
        print(f"  [ok] {table}.{col_name} existe déjà")
        return
    dialect = _dialect(engine)
    if dialect == "postgresql":
        ddl = f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column_ddl}'
    else:
        ddl = f"ALTER TABLE {table} ADD COLUMN {column_ddl}"
    with engine.begin() as conn:
        conn.execute(text(ddl))
    print(f"  [+] {table}.{col_name} ajouté")


def _unique_index_exists(engine, table: str, columns: list[str]) -> bool:
    """Un index/contrainte unique existe-t-il déjà sur exactement `columns` ?"""
    insp = inspect(engine)
    wanted = list(columns)
    for idx in insp.get_indexes(table) + insp.get_unique_constraints(table):
        cols = list(idx.get("column_names") or [])
        if idx.get("unique") and cols == wanted:
            return True
    return False


def _create_unique_index(engine, name: str, table: str, columns: list[str]) -> None:
    cols = ", ".join(columns)
    with engine.begin() as conn:
        conn.execute(
            text(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({cols})")
        )
    print(f"  [+] index unique {name} sur {table}({cols})")


def migrate(engine) -> dict:
    from webintel.database import models  # noqa: E402,F401  (enregistre les modèles)
    from webintel.database.models import Observation, Source, SourceObservation  # noqa: E402
    from webintel.entities.sources import get_or_create_source  # noqa: E402
    from webintel.database.base import SessionLocal as _SessionLocal  # noqa: E402

    stats = {"sources_created": 0, "observations_backfilled": 0,
             "history_backfilled": 0, "duplicates_removed": 0,
             "entities_stripped": 0}
    dialect = _dialect(engine)
    print(f"Dialecte : {dialect}")

    # 1. Table `sources` -----------------------------------------------------
    Base.metadata.tables["sources"].create(engine, checkfirst=True)
    print("  [ok] table `sources`")

    # 2. Colonnes manquantes -------------------------------------------------
    _add_column(engine, "source_observations", "source_id INTEGER")
    _add_column(engine, "source_observations", "value_hash VARCHAR(64)")
    _add_column(engine, "observations", "source_id INTEGER")
    _add_column(engine, "observations", "source_url TEXT")
    _add_column(engine, "observations", "source_domain VARCHAR(255)")

    session = _SessionLocal(bind=engine)
    try:
        # 3. Une Source par hôte déjà observé --------------------------------
        hosts = {
            row[0] for row in
            session.query(SourceObservation.source_domain).distinct().all()
            if row[0]
        }
        for host in sorted(hosts):
            source, created = _get_or_create_by_host(session, host)
            if created:
                stats["sources_created"] += 1
        session.commit()
        print(f"  [ok] {len(hosts)} hôte(s), {stats['sources_created']} source(s) créée(s)")

        host_to_id = {s.canonical_host: s.id for s in session.query(Source).all()}

        # 4a. Rattachement source_id des observations existantes -------------
        rows = (
            session.query(SourceObservation)
            .filter(SourceObservation.source_id.is_(None))
            .all()
        )
        for row in rows:
            sid = host_to_id.get(row.source_domain or "")
            if sid:
                row.source_id = sid
                stats["observations_backfilled"] += 1
        session.commit()

        # 4b. Calcul de value_hash -------------------------------------------
        rows = (
            session.query(SourceObservation)
            .filter(SourceObservation.value_hash.is_(None))
            .all()
        )
        for row in rows:
            row.value_hash = value_hash_for(row.value)
        session.commit()
        print(f"  [ok] {len(rows)} value_hash calculé(s)")

        # 4c. Discipline v1.1.1 : `Entity.data` = identité/synthèse uniquement.
        # Les prix/disponibilités/notes hérités de la v1.1 sont retirés de
        # `data` — ils restent disponibles dans `source_observations`.
        from webintel.database.models import Entity  # noqa: E402
        from webintel.entities.registry import _VOLATILE_FIELDS  # noqa: E402

        for entity in session.query(Entity).all():
            data = entity.data or {}
            volatile = _VOLATILE_FIELDS.get(
                (entity.entity_type or "").lower(), frozenset())
            stripped = {k: v for k, v in data.items() if k not in volatile}
            if len(stripped) != len(data):
                entity.data = stripped
                stats["entities_stripped"] += 1
        session.commit()
        print(f"  [ok] {stats['entities_stripped']} entité(s) nettoyée(s) "
              f"(champs volatils retirés de `data`)")

        # 5. Provenance du journal des changements ---------------------------
        from webintel.database.models import PageSnapshot  # noqa: E402

        hist_rows = (
            session.query(Observation)
            .filter(
                Observation.source_id.is_(None),
                Observation.snapshot_id.is_not(None),
            )
            .all()
        )
        snapshots = {
            s.id: s.url for s in session.query(PageSnapshot).all()
        }
        for row in hist_rows:
            url = snapshots.get(row.snapshot_id or -1) or ""
            if not url:
                continue
            try:
                source = get_or_create_source(session, url)
            except ValueError:
                continue
            row.source_id = source.id
            row.source_url = url
            row.source_domain = source.canonical_host
            host_to_id[source.canonical_host] = source.id
            stats["history_backfilled"] += 1
        session.commit()
        print(f"  [ok] {stats['history_backfilled']} changement(s) rattaché(s) à leur source")

        # 6. Déduplication avant l'index unique -------------------------------
        dup_groups = (
            session.query(
                SourceObservation.entity_id,
                SourceObservation.source_id,
                SourceObservation.field,
                SourceObservation.value_hash,
            )
            .filter(SourceObservation.value_hash.is_not(None))
            .group_by(
                SourceObservation.entity_id,
                SourceObservation.source_id,
                SourceObservation.field,
                SourceObservation.value_hash,
            )
            .having(text("COUNT(*) > 1"))
            .all()
        )
        for entity_id, source_id, field, vhash in dup_groups:
            ids = [
                r[0] for r in session.query(SourceObservation.id)
                .filter(
                    SourceObservation.entity_id == entity_id,
                    SourceObservation.source_id == source_id,
                    SourceObservation.field == field,
                    SourceObservation.value_hash == vhash,
                )
                .order_by(SourceObservation.id)
                .all()
            ]
            for doomed in ids[1:]:
                session.query(SourceObservation).filter(
                    SourceObservation.id == doomed).delete()
                stats["duplicates_removed"] += 1
        session.commit()
        print(f"  [ok] {stats['duplicates_removed']} doublon(s) supprimé(s)")
    finally:
        session.close()

    # 7. Index unique (entity_id, source_id, field, value_hash) ----------------
    cols = ["entity_id", "source_id", "field", "value_hash"]
    if _unique_index_exists(engine, "source_observations", cols):
        print("  [ok] unicité (entity_id, source_id, field, value_hash) déjà garantie")
    else:
        _create_unique_index(
            engine,
            "uq_source_observations_entity_source_field_vhash",
            "source_observations",
            cols,
        )
    return stats


def _get_or_create_by_host(session, host: str):
    """Get-or-create d'une Source à partir d'un hôte déjà normalisé."""
    from webintel.database.helpers import get_or_create  # noqa: E402
    from webintel.database.models import Source  # noqa: E402

    return get_or_create(
        session, Source,
        defaults={"name": host, "meta": {}},
        canonical_host=host,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migration v1.1 -> v1.1.1 (table Source, discipline des observations)."
    )
    parser.add_argument("--database-url", default=None,
                        help="URL SQLAlchemy (défaut : DATABASE_URL ou SQLite dev)")
    args = parser.parse_args()

    url = args.database_url or os.environ.get("DATABASE_URL")
    if url:
        engine = create_engine(url)
        print(f"Base cible : {url.split('://')[0]}://…")
    else:
        from webintel.database.base import engine as dev_engine  # noqa: E402
        engine = dev_engine
        print("Base cible : SQLite de développement")

    stats = migrate(engine)
    print("Migration v1.1 -> v1.1.1 terminée :", stats)
    print("Relancez ce script pour vérifier l'idempotence (zéro création).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
