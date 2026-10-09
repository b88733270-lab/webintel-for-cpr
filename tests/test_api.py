"""Tests de l'API REST via TestClient (fetch 100 % mocké, zéro réseau)."""

import os

from webintel.crawler.fetcher import HybridFetcher

from tests.helpers import make_fetcher, wait_for

_SERVER_MODE = os.environ.get("REQUIRE_API_KEY") == "true"


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_projects_crud(client):
    if _SERVER_MODE:
        # En mode clé API, la création de projet est réservée au bootstrap.
        created = client.post("/api/projects", json={"name": "Projet Test API"})
        assert created.status_code == 403
        listed = client.get("/api/projects")
        assert listed.status_code == 200
        assert len(listed.json()) == 1  # uniquement le projet de la clé
        return
    created = client.post("/api/projects", json={"name": "Projet Test API"})
    assert created.status_code in (200, 201)
    project_id = created.json()["id"]
    assert project_id > 0

    listed = client.get("/api/projects")
    assert listed.status_code == 200
    assert any(p["id"] == project_id for p in listed.json())


def test_analyze_produit_mock(client, make_project, monkeypatch, html_produit):
    project_id = make_project("Projet Analyse")
    monkeypatch.setattr(
        HybridFetcher, "fetch",
        make_fetcher({"https://boutique.example.com/produit": html_produit}),
    )
    response = client.post("/api/analyze", json={
        "url": "https://boutique.example.com/produit",
        "project_id": project_id,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["page_type"] == "product"
    products = [e for e in body["entities"] if e["entity_type"] == "product"]
    assert products, "aucune entité produit dans la fiche intelligence"
    # Discipline v1.1.1 : AUCUN prix global dans `Entity.data` — le prix vit
    # dans les observations par source.
    assert "price" not in (products[0]["data"] or {})
    assert "availability" not in (products[0]["data"] or {})

    detail = client.get(f"/api/entities/{products[0]['id']}")
    assert detail.status_code == 200
    grouped = detail.json()["observations_by_source"]
    assert set(grouped) == {"boutique.example.com"}
    price_obs = [o for o in grouped["boutique.example.com"] if o["field"] == "price"]
    assert price_obs, "aucune observation de prix par source"
    assert price_obs[0]["value"]["amount"] == 49.99
    assert price_obs[0]["value"]["currency"] == "EUR"
    assert price_obs[0]["source_id"] is not None

    entities = client.get("/api/entities", params={"project_id": project_id})
    assert entities.status_code == 200
    assert len(entities.json()) >= 1


def test_export_entities_csv(client, make_project, monkeypatch, html_produit):
    project_id = make_project("Projet Export")
    monkeypatch.setattr(
        HybridFetcher, "fetch",
        make_fetcher({"https://boutique.example.com/produit": html_produit}),
    )
    analyze = client.post("/api/analyze", json={
        "url": "https://boutique.example.com/produit",
        "project_id": project_id,
    })
    assert analyze.status_code == 200

    created = client.post("/api/exports", json={
        "project_id": project_id, "kind": "entities", "format": "csv",
    })
    assert created.status_code == 200
    export_id = created.json()["id"]
    download_url = created.json()["download_url"]

    download = client.get(download_url)
    assert download.status_code == 200
    assert "Casque" in download.text


def test_crawl_mock_termine_completed(client, make_project, monkeypatch,
                                     html_site_index, html_produit,
                                     html_article, html_entreprise):
    project_id = make_project("Projet Crawl")
    base = "https://demo.example.com"
    pages = {
        base + "/": html_site_index,
        base + "/produit": html_produit,
        base + "/article": html_article,
        base + "/entreprise": html_entreprise,
    }
    monkeypatch.setattr(HybridFetcher, "fetch", make_fetcher(pages))

    created = client.post("/api/crawls", json={
        "project_id": project_id,
        "start_url": base + "/",
        "limits": {
            "max_pages": 5, "max_depth": 2, "delay_s": 0,
            "concurrency": 2, "obey_robots": False,
        },
    })
    assert created.status_code == 200
    job_id = created.json()["id"]

    def _completed():
        response = client.get(f"/api/crawls/{job_id}")
        assert response.status_code == 200
        state = response.json()
        return state if state["status"] == "completed" else None

    final = wait_for(_completed, timeout_s=30.0)
    assert final is not None, "le crawl mocké n'a pas atteint COMPLETED à temps"
    assert final["status"] == "completed"
    progress = final.get("progress") or {}
    assert (progress.get("summary") or {}).get("visited", 0) >= 3
