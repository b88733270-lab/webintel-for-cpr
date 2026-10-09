"""Tests pagination + index + migration idempotente (chantier robustesse).

Couvre :
- `offset` sur les recherches API plantes/composés (pages disjointes,
  stables, `offset` négatif rejeté) ;
- idempotence de la migration `_ensure_v22_performance_indexes` ;
- présence effective des index (EXPLAIN QUERY PLAN utilise l'index).
"""
import pytest

from webintel.database.base import _ensure_v22_performance_indexes, engine


def _creer_plantes(client, prefixe, n):
    for i in range(n):
        r = client.post(
            "/api/science/plants",
            json={"scientific_name": f"{prefixe} {i:02d}"},
        )
        assert r.status_code == 201, r.text


def test_pagination_plantes_pages_disjointes(client):
    _creer_plantes(client, "PagTest", 30)
    pages = []
    for offset in (0, 10, 20):
        r = client.get(
            "/api/science/plants",
            params={"q": "PagTest", "limit": 10, "offset": offset},
        )
        assert r.status_code == 200
        pages.append([x["id"] for x in r.json()])
    for page in pages:
        assert len(page) == 10
    # Pages disjointes : aucun doublon entre pages, 30 résultats uniques.
    vus = set()
    for page in pages:
        assert not (set(page) & vus)
        vus |= set(page)
    assert len(vus) == 30


def test_pagination_plantes_stable(client):
    _creer_plantes(client, "StabTest", 10)
    params = {"q": "StabTest", "limit": 5, "offset": 5}
    ids1 = [x["id"] for x in client.get("/api/science/plants", params=params).json()]
    ids2 = [x["id"] for x in client.get("/api/science/plants", params=params).json()]
    assert ids1 == ids2 and len(ids1) == 5


def test_pagination_composes_offset(client):
    for i in range(12):
        r = client.post("/api/science/compounds", json={"name": f"PagComp {i:02d}"})
        assert r.status_code == 201, r.text
    p1 = client.get(
        "/api/science/compounds", params={"q": "PagComp", "limit": 5, "offset": 0}
    )
    p2 = client.get(
        "/api/science/compounds", params={"q": "PagComp", "limit": 5, "offset": 5}
    )
    assert p1.status_code == p2.status_code == 200
    ids1 = {x["id"] for x in p1.json()}
    ids2 = {x["id"] for x in p2.json()}
    assert len(ids1) == len(ids2) == 5
    assert not (ids1 & ids2)


def test_pagination_offset_negatif_rejete(client):
    r = client.get("/api/science/plants", params={"q": "x", "offset": -1})
    assert r.status_code == 422


def test_pagination_recherche_unifiee_offset(client):
    _creer_plantes(client, "UniTest", 8)
    r = client.get(
        "/api/science/recherche",
        params={"q": "UniTest", "type": "plante", "limit": 5, "offset": 5},
    )
    assert r.status_code == 200
    assert len(r.json()["plantes"]) == 3


def test_migration_index_idempotente():
    # Deux exécutions : pas d'erreur, mêmes index.
    _ensure_v22_performance_indexes()
    _ensure_v22_performance_indexes()
    with engine.connect() as cx:
        noms = {
            row[0]
            for row in cx.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert "ix_plants_family" in noms
    assert "ix_plants_genus" in noms
    assert "ix_compounds_formula" in noms
    assert "ix_compounds_chemical_class" in noms


def test_explain_utilise_index_famille():
    with engine.connect() as cx:
        plan = "\n".join(
            str(row)
            for row in cx.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT id FROM plants WHERE family = 'Lamiaceae'"
            )
        )
    assert "ix_plants_family" in plan
