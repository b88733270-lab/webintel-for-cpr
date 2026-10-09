"""Tests de la discipline Entity / Observation (v1.1.1).

- `Entity.data` : identité/synthèse UNIQUEMENT — jamais de prix, de
  disponibilité ni de note ;
- les valeurs volatiles vivent UNIQUEMENT dans les observations sourcées
  (`SourceObservation`, une par source et par valeur distincte) ;
- la recherche `min_price`/`max_price` matche via les observations
  courantes par source (AUCUN prix global ambigu n'est exposé).
"""

import pytest

from webintel.core.pipeline import analyze_fetch_result
from webintel.database.models import Entity, SourceObservation
from webintel.search.engine import search_entities

from tests.helpers import make_fetch_result


def _product_html(price: str, availability: str = "InStock") -> str:
    return f"""<!DOCTYPE html><html lang="fr"><head>
<title>Casque Audio X200</title>
<script type="application/ld+json">{{"@context": "https://schema.org", "@type": "Product",
"name": "Casque Audio X200", "brand": {{"@type": "Brand", "name": "SonicBrand"}},
"sku": "X200-BLK",
"offers": {{"@type": "Offer", "price": "{price}", "priceCurrency": "EUR",
"availability": "https://schema.org/{availability}"}}}}</script>
</head><body><h1>Casque Audio X200</h1><p class="price">{price} €</p></body></html>"""


SOURCES = [
    ("https://www.site-a.fr/casque-x200", "499.00"),
    ("https://www.site-b.fr/casque-x200", "450.00"),
    ("https://www.site-c.fr/casque-x200", "430.00"),
]


@pytest.fixture()
def three_sources(db_session, make_project):
    project_id = make_project("Discipline")
    for url, price in SOURCES:
        analyze_fetch_result(
            db_session, project_id, make_fetch_result(url, _product_html(price))
        )
    entity = (
        db_session.query(Entity)
        .filter(Entity.project_id == project_id, Entity.entity_type == "product")
        .one()
    )
    return project_id, entity.id


def test_entity_data_sans_valeurs_volatiles(three_sources, db_session):
    """`Entity.data` : identité uniquement, pas de prix/disponibilité/note."""
    _project_id, entity_id = three_sources
    entity = db_session.get(Entity, entity_id)
    data = entity.data or {}
    assert data.get("name") == "Casque Audio X200"  # identité conservée
    assert data.get("brand") == "SonicBrand"
    assert data.get("sku") == "X200-BLK"
    for volatile in ("price", "availability", "rating", "review_count"):
        assert volatile not in data, f"Entity.data contient {volatile!r} (v1.1.1)"


def test_prix_vivent_dans_les_observations_sourcees(three_sources, db_session):
    """Trois prix (499/450/430 €) -> trois observations, une par source."""
    _project_id, entity_id = three_sources
    prices = (
        db_session.query(SourceObservation)
        .filter(SourceObservation.entity_id == entity_id,
                SourceObservation.field == "price")
        .all()
    )
    assert len(prices) == 3
    by_domain = {o.source_domain: o.value["amount"] for o in prices}
    assert by_domain == {
        "www.site-a.fr": 499.0, "www.site-b.fr": 450.0, "www.site-c.fr": 430.0,
    }
    # Chaque observation est rattachée à une Source (FK non nulle).
    assert all(o.source_id for o in prices)
    assert all(o.value_hash for o in prices)


def test_recherche_prix_via_observations(three_sources, db_session):
    """`min_price`/`max_price` matchent via les observations par source."""
    project_id, entity_id = three_sources
    # 450 € est observé sur site-b : l'entité matche min_price=450.
    results = search_entities(db_session, project_id=project_id, min_price=450)
    assert [r["id"] for r in results] == [entity_id]
    # max_price=440 : seul le 430 € de site-c matche, mais l'entité est trouvée.
    results = search_entities(db_session, project_id=project_id, max_price=440)
    assert [r["id"] for r in results] == [entity_id]
    # min_price=500 : aucun prix observé ne matche -> rien.
    assert search_entities(db_session, project_id=project_id, min_price=500) == []


def test_recherche_expose_prix_par_source_sans_prix_global(three_sources, db_session):
    """Le résultat expose `prices` (par source), jamais un prix global."""
    project_id, _entity_id = three_sources
    results = search_entities(db_session, project_id=project_id, q="Casque",
                              entity_type="product")
    assert len(results) == 1  # seule l'entité produit, pas les 3 pages web
    result = results[0]
    assert "price" not in result, "aucun prix global ambigu (v1.1.1)"
    amounts = sorted(p["amount"] for p in result["prices"])
    assert amounts == [430.0, 450.0, 499.0]
    assert {p["source_domain"] for p in result["prices"]} == {
        "www.site-a.fr", "www.site-b.fr", "www.site-c.fr",
    }


def test_api_detail_separe_identite_et_observations(client, db_session, three_sources):
    """`GET /api/entities/{id}` : identité vs observations par source."""
    _project_id, entity_id = three_sources
    response = client.get(f"/api/entities/{entity_id}")
    assert response.status_code == 200
    body = response.json()
    assert "price" not in (body["data"] or {})
    grouped = body["observations_by_source"]
    assert set(grouped) == {"www.site-a.fr", "www.site-b.fr", "www.site-c.fr"}
    for domain, obs in grouped.items():
        for ob in obs:
            assert ob["source_id"] is not None
            assert ob["source_domain"] == domain
    amounts = {d: next(o["value"]["amount"] for o in obs if o["field"] == "price")
               for d, obs in grouped.items()}
    assert amounts == {"www.site-a.fr": 499.0, "www.site-b.fr": 450.0,
                       "www.site-c.fr": 430.0}
