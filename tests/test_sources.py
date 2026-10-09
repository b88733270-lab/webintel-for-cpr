"""Tests de la table `Source` et de l'API sources (v1.1.1).

- chaque hôte observé devient une ligne `sources` (dédupliquée par
  `canonical_host`) ;
- `GET /api/sources` : liste cloisonnée au projet de la clé ;
- `GET /api/sources/{id}` : détail avec observations rattachées aux entités ;
- isolation inter-projets : les sources/observations d'un autre projet sont
  invisibles (404/403, jamais de fuite).
"""

import secrets

import pytest
from fastapi.testclient import TestClient

import webintel.main
from webintel.core.pipeline import analyze_fetch_result
from webintel.core.utils import sha256_hex
from webintel.database.models import ApiKey, Entity, Project, Source, SourceObservation

from tests.helpers import make_fetch_result


def _product_html(name: str, price: str) -> str:
    return f"""<!DOCTYPE html><html lang="fr"><head>
<title>{name}</title>
<script type="application/ld+json">{{"@context": "https://schema.org", "@type": "Product",
"name": "{name}",
"offers": {{"@type": "Offer", "price": "{price}", "priceCurrency": "EUR"}}}}</script>
</head><body><h1>{name}</h1><p class="price">{price} €</p></body></html>"""


@pytest.fixture()
def source_projects(db_session, make_project, server_bootstrap):
    """Deux projets avec données : P1 (site-a + site-b), P2 (site-b).

    En mode serveur, P1 est le projet de la clé de test (bootstrap, la
    création de projet par API étant réservée au bootstrap) et P2 un second
    projet + clé créés directement en DB. `key2` vaut `None` en mode local.
    """
    p1 = make_project("P1 sources")
    key2_plain = None
    if server_bootstrap is not None:
        project2 = Project(name="P2 sources")
        db_session.add(project2)
        db_session.flush()
        key2_plain = secrets.token_urlsafe(32)
        db_session.add(ApiKey(
            project_id=project2.id,
            name="clé-p2",
            key_hash=sha256_hex(key2_plain),
            prefix=key2_plain[:8],
            quotas={},
            revoked=False,
        ))
        db_session.commit()
        p2 = project2.id
    else:
        p2 = make_project("P2 sources")

    analyze_fetch_result(db_session, p1,
                         make_fetch_result("https://www.site-a.fr/p1",
                                           _product_html("Casque X1", "499.00")))
    analyze_fetch_result(db_session, p1,
                         make_fetch_result("https://www.site-b.fr/p1",
                                           _product_html("Casque X1", "450.00")))
    analyze_fetch_result(db_session, p2,
                         make_fetch_result("https://www.site-b.fr/p2",
                                           _product_html("Enceinte Y", "99.00")))
    return {"p1": p1, "p2": p2, "key2": key2_plain}


def _server_mode(source_projects) -> bool:
    return source_projects["key2"] is not None


def test_sources_dedup_par_hote(source_projects, db_session):
    """Une ligne `sources` par hôte, partagée entre projets."""
    hosts = sorted(s.canonical_host for s in db_session.query(Source).all())
    assert hosts == ["www.site-a.fr", "www.site-b.fr"]
    # Toutes les observations de prix pointent vers une source (FK non nulle).
    orphans = (
        db_session.query(SourceObservation)
        .filter(SourceObservation.field == "price",
                SourceObservation.source_id.is_(None))
        .count()
    )
    assert orphans == 0


def test_api_sources_liste_et_detail(client, source_projects, db_session):
    """Liste + détail cloisonnés au projet (P1)."""
    p1 = source_projects["p1"]
    response = client.get("/api/sources", params={"project_id": p1})
    assert response.status_code == 200, response.text
    listed = response.json()
    assert {s["canonical_host"] for s in listed} == {"www.site-a.fr", "www.site-b.fr"}

    source_a = next(s for s in listed if s["canonical_host"] == "www.site-a.fr")
    detail = client.get(f"/api/sources/{source_a['id']}",
                        params={"project_id": p1})
    assert detail.status_code == 200
    body = detail.json()
    assert body["canonical_host"] == "www.site-a.fr"
    assert body["observation_count"] >= 1
    assert len(body["observations"]) == body["observation_count"]
    # Chaque observation du détail est rattachée à une entité du projet P1.
    owned = {e.id for e in db_session.query(Entity)
             .filter(Entity.project_id == p1).all()}
    assert {o["entity_id"] for o in body["observations"]} <= owned
    assert {o["entity_id"] for o in body["observations"]} != set()


def test_api_sources_isolation_inter_projets(client, source_projects, db_session):
    """Le projet 2 ne voit que ses sources ; le projet 1 ne voit pas le projet 2."""
    p1, p2 = source_projects["p1"], source_projects["p2"]
    if _server_mode(source_projects):
        # Client authentifié avec la clé du projet 2.
        with TestClient(webintel.main.app,
                        headers={"X-API-Key": source_projects["key2"]}) as c2:
            # Projet de la clé par défaut : uniquement site-b.
            listed_p2 = c2.get("/api/sources")
            assert listed_p2.status_code == 200
            assert {s["canonical_host"] for s in listed_p2.json()} == {"www.site-b.fr"}
            # Demander explicitement l'autre projet -> 403.
            assert c2.get("/api/sources",
                          params={"project_id": p1}).status_code == 403
        # Le client P1 ne peut pas forcer l'accès au projet 2 -> 403.
        assert client.get("/api/sources",
                          params={"project_id": p2}).status_code == 403
        listed_p1 = client.get("/api/sources",
                               params={"project_id": p1}).json()
    else:
        listed_p2 = client.get("/api/sources",
                               params={"project_id": p2}).json()
        assert {s["canonical_host"] for s in listed_p2} == {"www.site-b.fr"}
        listed_p1 = client.get("/api/sources",
                               params={"project_id": p1}).json()

    assert {s["canonical_host"] for s in listed_p1} == {"www.site-a.fr", "www.site-b.fr"}

    # Détail d'une source : seules les observations du projet demandé.
    site_b = next(s for s in listed_p1 if s["canonical_host"] == "www.site-b.fr")
    detail = client.get(f"/api/sources/{site_b['id']}",
                        params={"project_id": p1})
    assert detail.status_code == 200
    owned_p1 = {e.id for e in db_session.query(Entity)
                .filter(Entity.project_id == p1).all()}
    assert {o["entity_id"] for o in detail.json()["observations"]} <= owned_p1

    # Source inexistante -> 404.
    assert client.get("/api/sources/999999",
                      params={"project_id": p1}).status_code == 404


def test_api_sources_sans_projet_explicite(client, source_projects):
    """Sans `project_id` : mode local -> liste vide ; mode serveur -> projet
    de la clé par défaut (cloisonnement)."""
    response = client.get("/api/sources")
    assert response.status_code == 200
    if _server_mode(source_projects):
        assert {s["canonical_host"] for s in response.json()} == {
            "www.site-a.fr", "www.site-b.fr"}
    else:
        assert response.json() == []
