
"""Tests d'isolation multi-projet / clés API (P0-1).

Scénario de l'audit : projet 1 + projet 2, clé API rattachée au projet 1.
Avant le correctif, `GET /api/projects/2` avec la clé du projet 1
répondait 200 ; il doit désormais répondre 403 — et ce pour CHAQUE
famille d'endpoints (projets, entités, historique, événements, moniteurs,
alertes, exports + téléchargements, crawls + pilotage, recherche, usage).
"""

import os

import secrets

import pytest

import webintel.config.settings as _settings_mod
from webintel.core.exceptions import FetchError
from webintel.core.utils import sha256_hex
from webintel.database.models import (
    Alert,
    ApiKey,
    CrawlJob,
    Entity,
    Event,
    Export,
    Monitor,
    Project,
)


@pytest.fixture()
def key_mode(monkeypatch):
    """Active `REQUIRE_API_KEY=true` pour la durée du test (puis restaure).

    Note : `Settings` lit l'environnement à l'import (défauts évalués dans
    le corps de la classe) ; on force donc l'attribut sur l'instance
    singleton plutôt que de se fier à la variable d'environnement.
    """
    settings = _settings_mod.Settings()
    settings.REQUIRE_API_KEY = True
    _settings_mod._settings = settings
    yield
    _settings_mod._settings = None


def _project_with_key(db_session, name: str):
    """Crée un projet + une clé API (directement en DB : bootstrap)."""
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    plain = secrets.token_urlsafe(32)
    key = ApiKey(
        project_id=project.id,
        name=f"clé-{name}",
        key_hash=sha256_hex(plain),
        prefix=plain[:8],
        quotas={},
        revoked=False,
    )
    db_session.add(key)
    db_session.commit()
    return project, plain


@pytest.fixture()
def two_projects(db_session):
    """Deux projets isolés, chacun avec sa clé."""
    p1, k1 = _project_with_key(db_session, "Projet 1")
    p2, k2 = _project_with_key(db_session, "Projet 2")
    return {"p1": p1.id, "k1": k1, "p2": p2.id, "k2": k2}


def _h(plain: str) -> dict:
    return {"X-API-Key": plain}


# ---------------------------------------------------------------------------
# Projets & clés
# ---------------------------------------------------------------------------

def test_projets_isolation(client, anon_client, two_projects, key_mode):
    """Le cas exact de l'audit : clé du projet 1 -> GET /api/projects/2 = 403."""
    h1, p1, p2 = _h(two_projects["k1"]), two_projects["p1"], two_projects["p2"]

    assert client.get(f"/api/projects/{p2}", headers=h1).status_code == 403
    assert client.get(f"/api/projects/{p1}", headers=h1).status_code == 200

    # Liste cloisonnée : uniquement le projet de la clé.
    body = client.get("/api/projects", headers=h1).json()
    assert [p["id"] for p in body] == [p1]

    # Création de projet interdite en mode clé (bootstrap uniquement).
    assert client.post("/api/projects", json={"name": "X"}, headers=h1).status_code == 403

    # Clés d'un autre projet : 403 dans les deux sens.
    assert client.get(f"/api/projects/{p2}/api-keys", headers=h1).status_code == 403
    r = client.post(f"/api/projects/{p2}/api-keys", json={"name": "y"}, headers=h1)
    assert r.status_code == 403

    # Sans clé du tout : 401 (client anonyme : aucune clé injectée par défaut).
    assert anon_client.get(f"/api/projects/{p1}").status_code == 401
    # Mauvaise clé : 401.
    assert client.get(f"/api/projects/{p1}", headers=_h("nimporte-quoi")).status_code == 401


@pytest.mark.skipif(
    os.environ.get("REQUIRE_API_KEY") == "true",
    reason="comportement bypass local uniquement (sans REQUIRE_API_KEY)",
)
def test_bypass_mode_inchange(anon_client, two_projects):
    """Sans REQUIRE_API_KEY (défaut dev), le comportement v1 est préservé."""
    p2 = two_projects["p2"]
    assert anon_client.get(f"/api/projects/{p2}").status_code == 200
    assert len(anon_client.get("/api/projects").json()) == 2


# ---------------------------------------------------------------------------
# Moniteurs
# ---------------------------------------------------------------------------

def _monitor(db_session, project_id: int, name: str) -> Monitor:
    m = Monitor(project_id=project_id, name=name, target_type="url",
                target="https://example.com/", interval_minutes=60,
                active=True, config={})
    db_session.add(m)
    db_session.commit()
    return m


def test_moniteurs_isolation(client, db_session, two_projects, key_mode):
    h1, p1, p2 = _h(two_projects["k1"]), two_projects["p1"], two_projects["p2"]
    m1 = _monitor(db_session, p1, "m1")
    m2 = _monitor(db_session, p2, "m2")

    assert client.get(f"/api/monitors/{m2.id}", headers=h1).status_code == 403
    assert client.get(f"/api/monitors/{m1.id}", headers=h1).status_code == 200

    # Liste cloisonnée + filtre explicite vers l'autre projet = 403.
    assert [m["id"] for m in client.get("/api/monitors", headers=h1).json()] == [m1.id]
    assert client.get("/api/monitors", params={"project_id": p2}, headers=h1).status_code == 403

    # Création : 403 si project_id étranger, 201 dans son projet.
    payload = {"project_id": p2, "name": "x", "target_type": "url",
               "target": "https://example.com/", "interval_minutes": 30}
    assert client.post("/api/monitors", json=payload, headers=h1).status_code == 403
    payload["project_id"] = p1
    assert client.post("/api/monitors", json=payload, headers=h1).status_code == 201

    # Suppression / exécution d'un moniteur étranger : 403, sans effet.
    assert client.delete(f"/api/monitors/{m2.id}", headers=h1).status_code == 403
    assert db_session.get(Monitor, m2.id) is not None
    assert client.post(f"/api/monitors/{m2.id}/run", headers=h1).status_code == 403


# ---------------------------------------------------------------------------
# Entités, historique, recherche
# ---------------------------------------------------------------------------

def _entity(db_session, project_id: int, key: str) -> Entity:
    e = Entity(project_id=project_id, entity_key=key, entity_type="product",
               name=f"Entité {key}", data={}, sources=[])
    db_session.add(e)
    db_session.commit()
    return e


def test_entites_isolation(client, db_session, two_projects, key_mode):
    h1, p1, p2 = _h(two_projects["k1"]), two_projects["p1"], two_projects["p2"]
    e1 = _entity(db_session, p1, "product:aaa")
    e2 = _entity(db_session, p2, "product:bbb")

    assert client.get(f"/api/entities/{e2.id}", headers=h1).status_code == 403
    r = client.get(f"/api/entities/{e1.id}", headers=h1)
    assert r.status_code == 200
    assert "observations_by_source" in r.json()

    assert client.get(f"/api/entities/{e2.id}/history", headers=h1).status_code == 403
    assert client.get(f"/api/entities/{e1.id}/history", headers=h1).status_code == 200

    assert client.get("/api/entities", params={"project_id": p2}, headers=h1).status_code == 403
    assert [e["id"] for e in client.get("/api/entities", headers=h1).json()["results"]] == [e1.id]

    # Recherche globale : même cloisonnement.
    assert client.get("/api/search", params={"project_id": p2}, headers=h1).status_code == 403
    found = client.get("/api/search", headers=h1).json()
    assert [e["id"] for e in found["results"]] == [e1.id]


# ---------------------------------------------------------------------------
# Événements & alertes
# ---------------------------------------------------------------------------

def test_evenements_alertes_isolation(client, db_session, two_projects, key_mode):
    h1, p1, p2 = _h(two_projects["k1"]), two_projects["p1"], two_projects["p2"]
    db_session.add(Event(project_id=p1, type="page_changed", url="https://a.example/"))
    db_session.add(Event(project_id=p2, type="page_changed", url="https://b.example/"))
    db_session.add(Alert(project_id=p1, channel="log", rule={}, payload={}, status="sent"))
    db_session.add(Alert(project_id=p2, channel="log", rule={}, payload={}, status="sent"))
    db_session.commit()

    assert client.get("/api/events", params={"project_id": p2}, headers=h1).status_code == 403
    events = client.get("/api/events", headers=h1).json()
    assert {e["url"] for e in events} == {"https://a.example/"}

    assert client.get("/api/alerts", params={"project_id": p2}, headers=h1).status_code == 403
    alerts = client.get("/api/alerts", headers=h1).json()
    assert len(alerts) == 1


# ---------------------------------------------------------------------------
# Crawls & pilotage des jobs
# ---------------------------------------------------------------------------

def _job(db_session, project_id: int) -> CrawlJob:
    j = CrawlJob(project_id=project_id, kind="crawl", status="completed",
                 params={}, progress={})
    db_session.add(j)
    db_session.commit()
    return j


def test_crawls_isolation(client, db_session, two_projects, key_mode):
    h1, p1, p2 = _h(two_projects["k1"]), two_projects["p1"], two_projects["p2"]
    j1 = _job(db_session, p1)
    j2 = _job(db_session, p2)

    assert client.get(f"/api/crawls/{j2.id}", headers=h1).status_code == 403
    assert client.get(f"/api/crawls/{j1.id}", headers=h1).status_code == 200

    assert client.get("/api/crawls", params={"project_id": p2}, headers=h1).status_code == 403
    assert [j["id"] for j in client.get("/api/crawls", headers=h1).json()] == [j1.id]

    # Soumission vers un autre projet : 403 AVANT toute création de job.
    before = db_session.query(CrawlJob).count()
    r = client.post("/api/crawls",
                    json={"project_id": p2, "start_url": "https://example.com/"},
                    headers=h1)
    assert r.status_code == 403
    assert db_session.query(CrawlJob).count() == before

    # Pilotage d'un job étranger : 403.
    assert client.post(f"/api/crawls/{j2.id}/cancel", headers=h1).status_code == 403
    assert client.post(f"/api/crawls/{j2.id}/pause", headers=h1).status_code == 403
    assert client.post(f"/api/crawls/{j2.id}/resume", headers=h1).status_code == 403


# ---------------------------------------------------------------------------
# Exports & téléchargements
# ---------------------------------------------------------------------------

def test_exports_isolation(client, db_session, two_projects, key_mode):
    h1, p1, p2 = _h(two_projects["k1"]), two_projects["p1"], two_projects["p2"]
    x2 = Export(project_id=p2, kind="entities", format="csv", params={},
                status="failed", file_path=None)
    db_session.add(x2)
    db_session.commit()

    # Création vers un autre projet : 403.
    r = client.post("/api/exports",
                    json={"project_id": p2, "kind": "entities", "format": "csv"},
                    headers=h1)
    assert r.status_code == 403

    # Téléchargement : 403 AVANT la vérification du fichier...
    assert client.get(f"/api/exports/{x2.id}/download", headers=h1).status_code == 403
    # ... et 404 (fichier manquant) pour le propriétaire légitime.
    assert client.get(f"/api/exports/{x2.id}/download",
                      headers=_h(two_projects["k2"])).status_code == 404


# ---------------------------------------------------------------------------
# Analyse d'URL
# ---------------------------------------------------------------------------

def test_analyze_isolation(client, two_projects, key_mode, monkeypatch):
    """Le contrôle projet précède toute collecte réseau (403 sans fetch)."""
    from webintel.core import pipeline as core_pipeline

    def _boom(self, url):
        raise FetchError("réseau neutralisé pour le test")

    monkeypatch.setattr(core_pipeline.HybridFetcher, "fetch", _boom)
    h1, p1, p2 = _h(two_projects["k1"]), two_projects["p1"], two_projects["p2"]

    body = {"project_id": p2, "url": "https://example.com/"}
    assert client.post("/api/analyze", json=body, headers=h1).status_code == 403

    # Même projet : le contrôle passe, l'échec vient du fetch mocké (502).
    body = {"project_id": p1, "url": "https://example.com/"}
    assert client.post("/api/analyze", json=body, headers=h1).status_code == 502


# ---------------------------------------------------------------------------
# Compteurs d'usage
# ---------------------------------------------------------------------------

def test_usage_isolation(client, db_session, two_projects, key_mode):
    """`/api/usage` ne fuit plus les compteurs des autres projets."""
    h1, p1 = _h(two_projects["k1"]), two_projects["p1"]
    _entity(db_session, p1, "product:usage")
    usage = client.get("/api/usage", headers=h1).json()
    assert usage["projects"] == 1
    assert usage["entities"] == 1
