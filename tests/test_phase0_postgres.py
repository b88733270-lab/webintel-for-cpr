"""Tests Phase 0 — PostgreSQL (chantier 0.4).

TOUS les tests de ce module sont skippés proprement quand aucun serveur
PostgreSQL n'est joignable : `pytestmark = pytest.mark.skipif(...)` basé
sur `_pg_available()`, qui tente `psycopg.connect(url, connect_timeout=3)`.

- URL par défaut : `postgresql+psycopg://webintel:webintel@localhost:5432/webintel_test`
- Surcharge : variable d'environnement `WEBINTEL_TEST_PG_URL`.

DÉPENDANCE chantier 0.2 : `webintel.database.helpers.upsert_source_observation`
n'est pas encore livré au moment de l'écriture — le test qui l'exerce
directement est skippé avec un motif explicite tant que la fonction manque.

Aucun réseau externe par ailleurs : seule une connexion TCP locale vers
PostgreSQL est tentée (timeout court), jamais de DNS/internet.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

try:
    from webintel.database import helpers as _db_helpers

    _upsert_source_observation = getattr(
        _db_helpers, "upsert_source_observation", None
    )
except Exception:  # pragma: no cover - module non importable
    _upsert_source_observation = None

_REQUIERT_UPSERT_HELPER = pytest.mark.skipif(
    _upsert_source_observation is None,
    reason="chantier 0.2 : webintel.database.helpers.upsert_source_observation "
    "non livré",
)

_PG_URL = os.environ.get(
    "WEBINTEL_TEST_PG_URL",
    # NOTE : `127.0.0.1` plutôt que `localhost` — la fixture autouse
    # `_no_network_dns` de conftest neutralise la résolution DNS
    # (`localhost` -> IP fictive), mais laisse passer les IP littérales.
    "postgresql+psycopg://webintel:webintel@127.0.0.1:5432/webintel_test",
)


def _pg_available() -> bool:
    """Un serveur PostgreSQL est-il joignable (timeout court) ?"""
    try:
        import psycopg
    except Exception:
        return False
    dsn = _PG_URL
    if "://" in dsn:  # `postgresql+psycopg://…` -> DSN libpq `postgresql://…`
        scheme, rest = dsn.split("://", 1)
        scheme = {
            "postgresql+psycopg": "postgresql",
            "postgresql+psycopg2": "postgresql",
        }.get(scheme, scheme)
        dsn = f"{scheme}://{rest}"
    try:
        conn = psycopg.connect(dsn, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL indisponible : WEBINTEL_TEST_PG_URL non joignable "
    "(ou psycopg absent) — tous les tests PG sont skippés",
)

from webintel.database import models  # noqa: E402
from webintel.database.base import Base  # noqa: E402
from webintel.database.helpers import value_hash_for  # noqa: E402
# Table du chantier combinaisons (autre équipe) : référencée ici
# UNIQUEMENT pour l'ordre de nettoyage — le modèle n'est ni créé ni
# modifié par ce fichier.
from webintel.science import models as science_models  # noqa: E402
from webintel.entities.registry import (  # noqa: E402
    get_observations_by_source,
    record_source_observations,
    upsert_entity,
)
from webintel.entities.sources import get_or_create_source  # noqa: E402

# Ordre de suppression respectant les clés étrangères : dérivé
# topologiquement des métadonnées (enfants avant parents) — robuste aux
# tables ajoutées par les chantiers parallèles (combinaisons, labo, ...).
# Remplace l'ancienne liste manuelle (cf. conftest).
_TABLES_IN_DELETE_ORDER = [
    table.name for table in reversed(Base.metadata.sorted_tables)
]


@pytest.fixture()
def pg_engine():
    """Moteur SQLAlchemy vers PostgreSQL (base créée si besoin)."""
    engine = sa.create_engine(_PG_URL, connect_args={"connect_timeout": 3})
    Base.metadata.create_all(engine)  # équivalent de `init_db()` sur PG
    yield engine
    engine.dispose()


@pytest.fixture()
def pg_session(pg_engine):
    """Session sur PostgreSQL, nettoyée après chaque test (pas de pollution)."""
    Session = sa.orm.sessionmaker(
        bind=pg_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = Session()
    yield session
    session.rollback()
    # Suppression table-par-table dans l'ordre topologique (enfants avant
    # parents). Volontairement PAS de TRUNCATE : TRUNCATE prend un verrou
    # AccessExclusive et peut se dead locker avec des threads encore actifs
    # (cf. test_pg_concurrence_upsert_idempotent) ; les DELETE simples
    # n'ont besoin que de RowExclusiveLock et ne se bloquent pas ainsi.
    for table_name in _TABLES_IN_DELETE_ORDER:
        session.execute(
            sa.text(f'DELETE FROM "{table_name}"'))
    session.commit()
    session.close()


def _table_names(pg_engine) -> set[str]:
    return set(sa.inspect(pg_engine).get_table_names())


# ---------------------------------------------------------------------------
# Schéma
# ---------------------------------------------------------------------------


def test_pg_tables_creees(pg_engine):
    """`init_db()` (create_all) crée toutes les tables sur base PG vide."""
    names = _table_names(pg_engine)
    for expected in (
        "projects",
        "api_keys",
        "entities",
        "sources",
        "source_observations",
        "observations",
        "page_snapshots",
        "monitors",
        "alerts",
        "events",
        "crawl_jobs",
        "exports",
    ):
        assert expected in names, f"table manquante sur PG : {expected}"


def test_pg_index_presents(pg_engine):
    """Les index critiques de `source_observations` existent sur PG."""
    indexes = {
        ix["name"] for ix in sa.inspect(pg_engine).get_indexes("source_observations")
    }
    assert "ix_source_observations_entity_field_domain" in indexes
    assert "ix_source_observations_entity_source" in indexes
    assert "uq_source_observations_entity_source_field_vhash" in {
        uc["name"]
        for uc in sa.inspect(pg_engine).get_unique_constraints("source_observations")
    }


# ---------------------------------------------------------------------------
# JSON + contrainte unique
# ---------------------------------------------------------------------------


def test_pg_json_insert_select(pg_session):
    """Une valeur JSON (dict) survit à l'aller-retour sur PostgreSQL."""
    project = models.Project(name="PG JSON")
    pg_session.add(project)
    pg_session.flush()
    entity = models.Entity(
        project_id=project.id,
        entity_key="product:pg-json",
        entity_type="product",
        name="Casque X200",
        data={"name": "Casque X200", "specs": {"poids_g": 250}},
        sources=["https://www.boutique.fr/x200"],
    )
    pg_session.add(entity)
    pg_session.commit()
    pg_session.expunge_all()

    relu = pg_session.query(models.Entity).filter_by(entity_key="product:pg-json").one()
    assert relu.data == {"name": "Casque X200", "specs": {"poids_g": 250}}


def test_pg_contrainte_unique_observation(pg_session):
    """Doublon `(entity_id, source_id, field, value_hash)` -> IntegrityError."""
    project = models.Project(name="PG unique")
    pg_session.add(project)
    pg_session.flush()
    entity = models.Entity(
        project_id=project.id, entity_key="product:pg-uq",
        entity_type="product", name="X", data={}, sources=[],
    )
    pg_session.add(entity)
    pg_session.flush()
    source = get_or_create_source(pg_session, "https://www.boutique.fr/x")
    value = {"amount": 50.0, "currency": "EUR"}

    def _row():
        return models.SourceObservation(
            entity_id=entity.id,
            source_id=source.id,
            source_url="https://www.boutique.fr/x",
            source_domain=source.canonical_host,
            field="price",
            value=value,
            value_hash=value_hash_for(value),
            confidence=1.0,
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    pg_session.add(_row())
    pg_session.commit()
    pg_session.add(_row())
    with pytest.raises(sa.exc.IntegrityError):
        pg_session.commit()
    pg_session.rollback()
    assert (
        pg_session.query(models.SourceObservation)
        .filter_by(entity_id=entity.id, field="price")
        .count()
        == 1
    )


# ---------------------------------------------------------------------------
# Upsert d'observations sur PostgreSQL
# ---------------------------------------------------------------------------


def _prix(montant: float) -> dict:
    return {"name": "Casque X200", "price": {"amount": montant, "currency": "EUR"}}


def test_pg_upsert_50_60_50(pg_session):
    """50 € (J1) -> 60 € (J2) -> 50 € (J3) : 2 valeurs distinctes sur PG."""
    project = models.Project(name="PG upsert")
    pg_session.add(project)
    pg_session.commit()
    entity = upsert_entity(
        pg_session, project.id, "product", _prix(50.0),
        "https://www.boutique.fr/x200",
    )
    pg_session.commit()

    j1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    j2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    j3 = datetime(2026, 1, 3, tzinfo=timezone.utc)
    url = "https://www.boutique.fr/x200"
    assert record_source_observations(
        pg_session, entity, "product", _prix(50.0), url, observed_at=j1
    ) >= 1
    pg_session.commit()
    assert record_source_observations(
        pg_session, entity, "product", _prix(60.0), url, observed_at=j2
    ) >= 1
    pg_session.commit()
    record_source_observations(pg_session, entity, "product", _prix(50.0), url,
                               observed_at=j3)
    pg_session.commit()

    rows = (
        pg_session.query(models.SourceObservation)
        .filter(
            models.SourceObservation.entity_id == entity.id,
            models.SourceObservation.field == "price",
        )
        .all()
    )
    montants = {r.value["amount"] for r in rows}
    assert montants == {50.0, 60.0}, "2 valeurs distinctes attendues sur PG"
    assert len(rows) == 2, "aucun doublon de valeur sur PG"

    # Lecture par source : la valeur « actuelle » est bien exposée.
    par_source = get_observations_by_source(pg_session, entity.id)
    prix = [o for o in par_source["www.boutique.fr"] if o.field == "price"]
    assert len(prix) == 1


# ---------------------------------------------------------------------------
# Concurrence (threads) : idempotence réelle sous contention sur PG
# ---------------------------------------------------------------------------


def test_pg_concurrence_upsert_idempotent(pg_engine):
    """8 threads x même observation -> 1 seule `SourceObservation` sur PG."""
    import threading
    import uuid

    Session = sa.orm.sessionmaker(
        bind=pg_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    # Nom unique : ce test n'utilise pas la fixture `pg_session` (qui
    # TRUNCATE en teardown), il doit donc être auto-nettoyant ET ne pas
    # entrer en collision avec les résidus d'un run précédent/interrompu.
    nom_projet = f"PG concurrence {uuid.uuid4().hex[:8]}"
    try:
        session = Session()
        project = models.Project(name=nom_projet)
        session.add(project)
        session.commit()
        entity = upsert_entity(
            session, project.id, "product", _prix(50.0),
            "https://www.boutique.fr/conc",
        )
        session.commit()
        entity_id = entity.id
        session.close()

        url = "https://www.boutique.fr/conc"
        erreurs: list = []
        barriere = threading.Barrier(8)

        def cible():
            s = Session()
            try:
                barriere.wait(timeout=15)
                ent = s.get(models.Entity, entity_id)
                record_source_observations(
                    s, ent, "product", _prix(50.0), url,
                    observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
                s.commit()
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                erreurs.append(exc)
            finally:
                s.close()

        threads = [threading.Thread(target=cible) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        # Aucun thread ne doit survivre au join : un thread attardé
        # committant APRÈS le nettoyage créerait des lignes fantômes qui
        # feraient échouer les teardowns/tests suivants.
        attardes = [t for t in threads if t.is_alive()]
        assert not attardes, (
            f"{len(attardes)} thread(s) encore actif(s) après 60 s"
        )
        assert erreurs == [], f"erreurs de threads sur PG : {erreurs!r}"

        verif = Session()
        try:
            nombre = (
                verif.query(models.SourceObservation)
                .filter(
                    models.SourceObservation.entity_id == entity_id,
                    models.SourceObservation.field == "price",
                )
                .count()
            )
        finally:
            verif.close()
        assert nombre == 1, f"doublons sous contention PG : {nombre} lignes"
    finally:
        # Auto-nettoyage ciblé : supprime uniquement les lignes de CE test
        # (projet au nom unique). Pas de TRUNCATE ici : il prend un verrou
        # AccessExclusive et se dead locke si un thread attardé détient
        # encore un verrou ; les DELETE ciblés sont sûrs.
        with pg_engine.begin() as conn:
            pid = conn.execute(
                sa.text("SELECT id FROM projects WHERE name = :n"),
                {"n": nom_projet},
            ).scalar()
            if pid is not None:
                conn.execute(
                    sa.text(
                        "DELETE FROM source_observations WHERE entity_id IN "
                        "(SELECT id FROM entities WHERE project_id = :p)"
                    ),
                    {"p": pid},
                )
                conn.execute(
                    sa.text("DELETE FROM entities WHERE project_id = :p"),
                    {"p": pid},
                )
                conn.execute(
                    sa.text("DELETE FROM projects WHERE id = :p"), {"p": pid}
                )


@_REQUIERT_UPSERT_HELPER
def test_pg_upsert_source_observation_helper(pg_session):
    """`upsert_source_observation` (chantier 0.2) : (obj, created) sur PG."""
    project = models.Project(name="PG helper")
    pg_session.add(project)
    pg_session.flush()
    entity = models.Entity(
        project_id=project.id, entity_key="product:pg-helper",
        entity_type="product", name="X", data={}, sources=[],
    )
    pg_session.add(entity)
    pg_session.flush()
    source = get_or_create_source(pg_session, "https://www.boutique.fr/x")
    kwargs = dict(
        entity_id=entity.id,
        source_id=source.id,
        field="price",
        source_url="https://www.boutique.fr/x",
        source_domain=source.canonical_host,
    )

    obj, created = _upsert_source_observation(
        pg_session, value={"amount": 50.0, "currency": "EUR"}, **kwargs
    )
    assert created is True
    pg_session.commit()

    # Répétition de la même valeur : pas de doublon, `created` False.
    _obj2, created2 = _upsert_source_observation(
        pg_session, value={"amount": 50.0, "currency": "EUR"}, **kwargs
    )
    pg_session.commit()
    assert created2 is False
    assert (
        pg_session.query(models.SourceObservation)
        .filter_by(entity_id=entity.id, field="price")
        .count()
        == 1
    )

    # Nouvelle valeur : nouvelle ligne, `created` True.
    _obj3, created3 = _upsert_source_observation(
        pg_session, value={"amount": 60.0, "currency": "EUR"}, **kwargs
    )
    assert created3 is True
    pg_session.commit()
    assert (
        pg_session.query(models.SourceObservation)
        .filter_by(entity_id=entity.id, field="price")
        .count()
        == 2
    )
    assert obj.value["amount"] == 50.0
