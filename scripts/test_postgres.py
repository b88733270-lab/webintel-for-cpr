#!/usr/bin/env python3
"""Validation PostgreSQL de bout en bout pour WebIntel (chantier 0.1).

Rejoue sur une base PostgreSQL VIDE l'ensemble des vérifications du support PG :
création du schéma via `init_db()`, tables, clés étrangères, colonnes JSON,
contraintes uniques, index, TIMESTAMPTZ, et upsert.

Usage :
    DATABASE_URL='postgresql+psycopg://webintel:<mot-de-passe>@localhost:5432/webintel' \
        python scripts/test_postgres.py

Sans DATABASE_URL, utilise par défaut :
    postgresql+psycopg://webintel:webintel@localhost:5432/webintel

ATTENTION : le script SUPPRIME toutes les tables existantes avant de tester
(`Base.metadata.drop_all`). Ne le lancez jamais sur une base de production.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

# L'URL doit être posée AVANT tout import de webintel (le settings singleton
# et l'engine sont construits à l'import de webintel.database.base).
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://webintel:webintel@localhost:5432/webintel",
)
os.environ["DATABASE_URL"] = DATABASE_URL

# Rend le paquet `webintel` importable quel que soit le répertoire courant
# (le script vit dans scripts/, pas à la racine du dépôt).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from webintel.database import models  # noqa: E402  (enregistre les modèles)
# Les tables scientifiques / terrain / labo / workflows / versions sont
# enregistrées sur le même `Base.metadata` (voir `init_db()`) : elles
# DOIVENT être importées AVANT `Base.metadata.drop_all()` — sinon la
# suppression trie les tables dans un ordre qui ignore leurs clés
# étrangères (correctif 2026-09-23 : `DROP TABLE sources` échouait car
# `plant_compounds` non enregistré conservait sa FK vers `sources`).
from webintel.science import models as _science_models  # noqa: E402,F401
from webintel.offline import models as _offline_models  # noqa: E402,F401
from webintel.science import lab as _lab_models  # noqa: E402,F401
from webintel.science import versions as _versions_models  # noqa: E402,F401
from webintel.science import workflows as _workflows_models  # noqa: E402,F401
from webintel.database.base import Base, SessionLocal, engine, init_db  # noqa: E402

EXPECTED_TABLES = {
    "projects",
    "api_keys",
    "crawl_jobs",
    "page_snapshots",
    "entities",
    "observations",
    "sources",
    "source_observations",
    "events",
    "monitors",
}

EXPECTED_FKS = {
    # (table, colonne locale, table référencée, colonne référencée)
    ("api_keys", "project_id", "projects", "id"),
    ("entities", "project_id", "projects", "id"),
    ("observations", "entity_id", "entities", "id"),
    ("observations", "source_id", "sources", "id"),
    ("source_observations", "entity_id", "entities", "id"),
}

EXPECTED_INDEXES = {
    # (table, nom d'index)
    ("page_snapshots", "ix_page_snapshots_project_canonical_fetched"),
    ("observations", "ix_observations_entity_observed"),
    ("events", "ix_events_project_created"),
    ("monitors", "ix_monitors_active_last_run"),
}

EXPECTED_UNIQUE = {
    # (table, nom de contrainte unique)
    ("projects", None),  # unique=True sur projects.name -> contrainte auto
    ("entities", "uq_entities_project_key"),
    ("sources", None),  # unique=True sur sources.canonical_host
}

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"[{'OK' if ok else 'KO'}] {name}" + (f" — {detail}" if detail and not ok else ""))


def main() -> int:
    print(f"URL : {engine.url.render_as_string(hide_password=True)}")
    print(f"Dialecte : {engine.dialect.name} / driver : {engine.dialect.driver}")
    if engine.dialect.name != "postgresql":
        check("dialecte postgresql", False, f"dialecte={engine.dialect.name}")
        return 1
    check("dialecte postgresql (driver psycopg)", True)

    # 1. Base vide puis init_db()
    Base.metadata.drop_all(engine, checkfirst=True)
    init_db()
    check("init_db() sur base vide", True)

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    missing = EXPECTED_TABLES - tables
    check("toutes les tables créées", not missing,
          f"manquantes={sorted(missing)}" if missing else f"{len(tables)} tables")

    # 2. Clés étrangères
    fks_found = set()
    for table in EXPECTED_TABLES:
        for fk in insp.get_foreign_keys(table):
            referred = fk.get("referred_table")
            for col, refcol in zip(fk.get("constrained_columns", []),
                                   fk.get("referred_columns", [])):
                fks_found.add((table, col, referred, refcol))
    missing_fk = EXPECTED_FKS - fks_found
    check("clés étrangères présentes", not missing_fk,
          f"manquantes={sorted(missing_fk)}" if missing_fk else f"{len(fks_found)} FK")

    # 3. Index nommés
    idx_found = set()
    for table, _ in EXPECTED_INDEXES:
        for idx in insp.get_indexes(table):
            idx_found.add((table, idx["name"]))
    missing_idx = {n for n in EXPECTED_INDEXES if n not in idx_found}
    check("index nommés présents", not missing_idx,
          f"manquants={sorted(missing_idx)}" if missing_idx else f"{len(idx_found)} index")

    # 4. Contraintes uniques
    uq_ok = True
    uq_detail = ""
    for table, name in EXPECTED_UNIQUE:
        uqs = insp.get_unique_constraints(table)
        if name is None:
            if not uqs:
                uq_ok, uq_detail = False, f"aucune contrainte unique sur {table}"
        elif not any(u["name"] == name for u in uqs):
            uq_ok, uq_detail = False, f"{name} absent sur {table}"
    check("contraintes uniques présentes", uq_ok, uq_detail)

    # 5. TIMESTAMPTZ pour TZDateTime
    cols = {c["name"]: c for c in insp.get_columns("projects")}
    coltype = cols["created_at"]["type"]
    # str(TIMESTAMP(timezone=True)) rend "TIMESTAMP" : on teste l'attribut
    # plutôt que la forme textuelle.
    is_tz = bool(getattr(coltype, "timezone", False)) or (
        "TIME ZONE" in str(coltype).upper()
    )
    check("TZDateTime -> TIMESTAMPTZ", is_tz, repr(coltype))

    # 6. Insert/select JSON + timezone
    db = SessionLocal()
    try:
        project = models.Project(name="pg-validation")
        db.add(project)
        db.commit()

        aware = datetime(2026, 9, 22, 18, 30, tzinfo=timezone.utc)
        entity = models.Entity(
            project_id=project.id,
            entity_key="produit-1",
            entity_type="product",
            name="Produit test",
            data={"prix": 499.0, "devise": "EUR", "imbrique": {"a": [1, 2, 3]}},
            sources=["https://exemple.test/p1", "https://exemple.test/p2"],
            first_seen=aware,
            last_seen=aware,
        )
        db.add(entity)
        db.commit()
        db.expire_all()

        back = db.query(models.Entity).filter_by(entity_key="produit-1").one()
        check("roundtrip JSON (dict + liste)",
              back.data == {"prix": 499.0, "devise": "EUR", "imbrique": {"a": [1, 2, 3]}}
              and back.sources == ["https://exemple.test/p1", "https://exemple.test/p2"])
        check("timezone conservée (TIMESTAMPTZ)",
              back.first_seen.tzinfo is not None
              and back.first_seen.astimezone(timezone.utc) == aware,
              f"lu={back.first_seen!r}")

        # 7. Contrainte unique -> IntegrityError
        doublon = models.Entity(
            project_id=project.id, entity_key="produit-1",
            entity_type="product", name="Doublon",
        )
        db.add(doublon)
        try:
            db.commit()
            check("doublon (project_id, entity_key) rejeté", False, "aucune erreur levée")
        except IntegrityError:
            db.rollback()
            check("doublon (project_id, entity_key) rejeté (IntegrityError)", True)

        # 8. Upsert ON CONFLICT DO UPDATE
        stmt = (
            pg_insert(models.Entity)
            .values(project_id=project.id, entity_key="produit-1",
                    entity_type="product", name="Produit MAJ")
            .on_conflict_do_update(
                constraint="uq_entities_project_key",
                set_={"name": "Produit MAJ"},
            )
        )
        db.execute(stmt)
        db.commit()
        n = db.query(models.Entity).filter_by(
            project_id=project.id, entity_key="produit-1").count()
        final_name = db.query(models.Entity).filter_by(entity_key="produit-1").one().name
        check("upsert ON CONFLICT DO UPDATE",
              n == 1 and final_name == "Produit MAJ", f"n={n} name={final_name!r}")

        # 9. Sanity : version serveur
        version = db.execute(text("SELECT version()")).scalar()
        check("connexion serveur PostgreSQL", True)
        print(f"      serveur : {version.split(',')[0]}")
    finally:
        db.close()

    failed = [name for name, ok, _ in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} vérifications réussies.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
