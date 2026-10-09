"""Tests Phase 0 — régression (chantier 0.4).

Vérifie que la Phase 0 ne casse pas le comportement existant, sur SQLite,
de façon déterministe et sans réseau (le DNS fictif de `conftest.py` est
respecté : aucune connexion sortante).

DÉPENDANCE chantier 0.2 : `record_source_observations()` devient un « vrai
upsert » en interne (répéter une valeur déjà vue rafraîchit `observed_at`
au lieu d'être ignoré). Tant que ce comportement n'est pas livré — détecté
ici par la présence de `webintel.database.helpers.upsert_source_observation`
—, le test de la valeur « actuelle » après répétition est SKIPPÉ.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

from webintel.core.utils import sha256_hex
from webintel.database import helpers as _db_helpers
from webintel.database.models import ApiKey, Entity, Project, SourceObservation
from webintel.entities.registry import (
    get_entity,
    get_observations_by_source,
    record_source_observations,
    upsert_entity,
)

import webintel.config.settings as _settings_mod

_UPSERT_HELPER_DISPONIBLE = hasattr(_db_helpers, "upsert_source_observation")

_REQUIERT_UPSERT_VRAI = pytest.mark.skipif(
    not _UPSERT_HELPER_DISPONIBLE,
    reason="chantier 0.2 : record_source_observations « vrai upsert » "
    "(rafraîchissement d'observed_at à la répétition) non livré",
)

URL_A = "https://www.boutique-a.fr/casque-x200"
URL_B = "https://www.boutique-b.fr/casque-x200"


def _prix(montant: float) -> dict:
    return {"name": "Casque X200", "price": {"amount": montant, "currency": "EUR"}}


def _montant(value) -> float | None:
    """Extrait le montant d'une valeur d'observation `price` (dict ou brut)."""
    if isinstance(value, dict):
        for cle in ("amount", "value"):
            if cle in value:
                return float(value[cle])
        return None
    return float(value)


def _observations_prix(session, entity_id: int):
    return (
        session.query(SourceObservation)
        .filter(
            SourceObservation.entity_id == entity_id,
            SourceObservation.field == "price",
        )
        .order_by(SourceObservation.observed_at, SourceObservation.id)
        .all()
    )


# ---------------------------------------------------------------------------
# Pipeline canonique (léger) : upsert + observations par source + relecture
# ---------------------------------------------------------------------------


def test_pipeline_canonique_upsert_puis_observations(db_session, make_project):
    """`upsert_entity` + `record_source_observations` + relecture : le
    pipeline de base reste intact (discipline v1.1.1 : le prix volatil ne
    va PAS dans `Entity.data`)."""
    project_id = make_project("Régression pipeline")
    entity = upsert_entity(db_session, project_id, "product", _prix(50.0), URL_A)
    db_session.commit()

    # Discipline v1.1.1 : `price` est volatil -> exclu de `Entity.data`.
    assert "price" not in (entity.data or {})

    nouvelles = record_source_observations(
        db_session, entity, "product", _prix(50.0), URL_A
    )
    db_session.commit()
    assert nouvelles >= 1

    relu = get_entity(
        db_session, project_id,
        entity.entity_key,
    )
    assert relu is not None and relu.id == entity.id

    par_source = get_observations_by_source(db_session, entity.id)
    assert "www.boutique-a.fr" in par_source
    prix = [o for o in par_source["www.boutique-a.fr"] if o.field == "price"]
    assert len(prix) == 1
    assert _montant(prix[0].value) == 50.0
    assert prix[0].source_id is not None  # rattachement fort à `sources`


def test_pipeline_canonique_idempotent(db_session, make_project):
    """Ré-enregistrer la même valeur ne crée pas de doublon (SQLite)."""
    project_id = make_project("Régression idempotence")
    entity = upsert_entity(db_session, project_id, "product", _prix(50.0), URL_A)
    db_session.commit()

    assert record_source_observations(db_session, entity, "product",
                                      _prix(50.0), URL_A) >= 1
    db_session.commit()
    assert record_source_observations(db_session, entity, "product",
                                      _prix(50.0), URL_A) == 0
    db_session.commit()

    assert len(_observations_prix(db_session, entity.id)) == 1


# ---------------------------------------------------------------------------
# Isolation projets : une observation du projet A invisible du projet B
# ---------------------------------------------------------------------------


def _projet_db(db_session, nom: str) -> int:
    """Crée un projet directement en base (fonctionne en mode local ET serveur)."""
    projet = Project(name=nom)
    db_session.add(projet)
    db_session.flush()
    return projet.id


def test_isolation_projets_observations(db_session, make_project):
    """Une observation enregistrée pour le projet A n'apparaît pas en B."""
    # NB : création directe en DB plutôt que via `make_project` : en mode
    # serveur, la création de projet par API est interdite (403, bootstrap-only)
    # et `make_project` retourne le même projet pour A et B par design.
    pa = _projet_db(db_session, "Régression A")
    pb = _projet_db(db_session, "Régression B")

    ea = upsert_entity(db_session, pa, "product", _prix(50.0), URL_A)
    eb = upsert_entity(db_session, pb, "product", _prix(50.0), URL_B)
    db_session.commit()
    record_source_observations(db_session, ea, "product", _prix(50.0), URL_A)
    db_session.commit()

    obs_a = (
        db_session.query(SourceObservation)
        .join(Entity, SourceObservation.entity_id == Entity.id)
        .filter(Entity.project_id == pa)
        .all()
    )
    obs_b = (
        db_session.query(SourceObservation)
        .join(Entity, SourceObservation.entity_id == Entity.id)
        .filter(Entity.project_id == pb)
        .all()
    )
    assert len(obs_a) >= 1
    assert obs_b == []
    assert get_observations_by_source(db_session, eb.id) == {}


# ---------------------------------------------------------------------------
# Historique : 50 € (J1) -> 60 € (J2) -> 50 € (J3)
# ---------------------------------------------------------------------------


def test_historique_prix_deux_valeurs_distinctes(db_session, make_project):
    """Après 50->60->50, l'historique par source contient 2 valeurs, sans doublon."""
    project_id = make_project("Régression historique")
    entity = upsert_entity(db_session, project_id, "product", _prix(50.0), URL_A)
    db_session.commit()

    j1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    j2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    j3 = datetime(2026, 1, 3, tzinfo=timezone.utc)
    record_source_observations(db_session, entity, "product", _prix(50.0),
                               URL_A, observed_at=j1)
    db_session.commit()
    record_source_observations(db_session, entity, "product", _prix(60.0),
                               URL_A, observed_at=j2)
    db_session.commit()
    record_source_observations(db_session, entity, "product", _prix(50.0),
                               URL_A, observed_at=j3)
    db_session.commit()

    lignes = _observations_prix(db_session, entity.id)
    assert len(lignes) == 2, "une ligne par valeur DISTINCTE, sans doublon"
    assert {_montant(r.value) for r in lignes} == {50.0, 60.0}
    assert all(r.source_domain == "www.boutique-a.fr" for r in lignes)
    assert all(r.source_id is not None for r in lignes)


@_REQUIERT_UPSERT_VRAI
def test_valeur_actuelle_apres_repetition(db_session, make_project):
    """Dépend du chantier 0.2 (« vrai upsert ») : répéter 50 € en J3
    rafraîchit `observed_at`, donc la valeur « actuelle » (`observed_at`
    max) redevient 50 €."""
    project_id = make_project("Régression valeur actuelle")
    entity = upsert_entity(db_session, project_id, "product", _prix(50.0), URL_A)
    db_session.commit()

    j1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    j2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    j3 = datetime(2026, 1, 3, tzinfo=timezone.utc)
    record_source_observations(db_session, entity, "product", _prix(50.0),
                               URL_A, observed_at=j1)
    db_session.commit()
    record_source_observations(db_session, entity, "product", _prix(60.0),
                               URL_A, observed_at=j2)
    db_session.commit()
    record_source_observations(db_session, entity, "product", _prix(50.0),
                               URL_A, observed_at=j3)
    db_session.commit()

    lignes = _observations_prix(db_session, entity.id)
    assert len(lignes) == 2
    actuelle = max(lignes, key=lambda r: (r.observed_at, r.id))
    assert _montant(actuelle.value) == 50.0


# ---------------------------------------------------------------------------
# Sécurité de base : 401 sans clé en mode serveur simulé
# ---------------------------------------------------------------------------


@pytest.fixture()
def _mode_serveur():
    """`REQUIRE_API_KEY=true` sur le singleton (puis restaure)."""
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = True
    _settings_mod._settings = settings
    yield
    _settings_mod._settings = None


def test_sans_cle_en_mode_serveur_401(anon_client, db_session, _mode_serveur):
    """Sans clé API en mode serveur simulé : 401 (déjà câblé, non-régression)."""
    project = Project(name="Régression 401")
    db_session.add(project)
    db_session.flush()
    plain = secrets.token_urlsafe(32)
    db_session.add(
        ApiKey(
            project_id=project.id,
            name="clé-401",
            key_hash=sha256_hex(plain),
            prefix=plain[:8],
            quotas={},
            revoked=False,
        )
    )
    db_session.commit()

    assert anon_client.get(f"/api/projects/{project.id}").status_code == 401
    # Mauvaise clé : 401 aussi.
    assert anon_client.get(
        f"/api/projects/{project.id}", headers={"X-API-Key": "nimporte-quoi"}
    ).status_code == 401
