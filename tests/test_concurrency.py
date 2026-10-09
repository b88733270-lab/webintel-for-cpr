"""Tests de concurrence de la couche data (v1.1.1).

- `record_source_observations()` est idempotente : N threads qui enregistrent
  la MÊME observation ne créent qu'UNE ligne `SourceObservation` ;
- `get_or_create_source()` ne crée qu'UNE ligne `sources` sous contention ;
- `upsert_entity()` ne crée qu'UNE entité sous contention.

SQLite : le pool de test est fichier partagé + `check_same_thread=False`,
chaque thread a sa propre session ; les `IntegrityError` sont reprises par
les savepoints / `ON CONFLICT DO NOTHING`.
"""

import threading

import pytest

from webintel.database.base import SessionLocal
from webintel.database.models import (
    Entity, Project, Source, SourceObservation,
)
from webintel.entities import registry
from webintel.entities.sources import get_or_create_source


@pytest.fixture()
def project_id(db_session):
    project = Project(name="Concurrence")
    db_session.add(project)
    db_session.commit()
    return project.id


def _run_threads(count, target):
    errors = []
    barrier = threading.Barrier(count)

    def worker():
        try:
            barrier.wait(timeout=10)
            target()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == [], f"erreurs de threads : {errors!r}"


def test_observations_idempotentes_sous_threads(project_id):
    """8 threads x même observation -> 1 seule `SourceObservation`."""
    def target():
        session = SessionLocal()
        try:
            entity = registry.upsert_entity(
                session, project_id, "product",
                {"key": "product:conc-1", "name": "Produit Concurrent"},
                "https://www.conc.fr/x",
            )
            source = get_or_create_source(session, "https://www.conc.fr/x")
            session.flush()
            registry.record_source_observations(
                session, entity, "product",
                {"price": {"amount": 100.0, "currency": "EUR"}},
                "https://www.conc.fr/x",
                source=source,
            )
            session.commit()
        finally:
            session.close()

    _run_threads(8, target)

    session = SessionLocal()
    try:
        entities = session.query(Entity).filter(
            Entity.project_id == project_id,
            Entity.name == "Produit Concurrent").all()
        assert len(entities) == 1
        observations = session.query(SourceObservation).filter(
            SourceObservation.entity_id == entities[0].id,
            SourceObservation.field == "price").all()
        assert len(observations) == 1
        assert observations[0].value == {"amount": 100.0, "currency": "EUR"}
    finally:
        session.close()


def test_source_unique_sous_threads(project_id):
    """8 threads x `get_or_create_source` -> 1 seule ligne `sources`."""
    def target():
        session = SessionLocal()
        try:
            get_or_create_source(session, "https://www.unique.fr/page")
            session.commit()
        finally:
            session.close()

    _run_threads(8, target)

    session = SessionLocal()
    try:
        sources = session.query(Source).filter(
            Source.canonical_host == "www.unique.fr").all()
        assert len(sources) == 1
    finally:
        session.close()


def test_upsert_entity_unique_sous_threads(project_id):
    """8 threads x même entité -> 1 seule entité (check-then-insert protégé)."""
    def target():
        session = SessionLocal()
        try:
            registry.upsert_entity(
                session, project_id, "product",
                {"key": "product:conc-2", "name": "Produit Concurrent 2"},
                "https://www.conc.fr/y",
            )
            session.commit()
        finally:
            session.close()

    _run_threads(8, target)

    session = SessionLocal()
    try:
        entities = session.query(Entity).filter(
            Entity.project_id == project_id,
            Entity.name == "Produit Concurrent 2").all()
        assert len(entities) == 1
    finally:
        session.close()
