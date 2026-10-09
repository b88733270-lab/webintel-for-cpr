"""Tests de la provenance de l'historique (v1.1.1).

Chaque changement du journal (`Observation`) est directement attribué à la
source qui l'a produit : `source_id`, `source_url`, `source_domain`.
Scénario : baisse de prix sur le site A, puis sur le site B — chaque
entrée d'historique doit porter la bonne source.
"""

import pytest

from webintel.core.pipeline import analyze_fetch_result
from webintel.database.models import Entity, Observation, Source

from tests.helpers import make_fetch_result


def _product_html(price: str) -> str:
    return f"""<!DOCTYPE html><html lang="fr"><head>
<title>Casque Audio X200</title>
<script type="application/ld+json">{{"@context": "https://schema.org", "@type": "Product",
"name": "Casque Audio X200", "brand": {{"@type": "Brand", "name": "SonicBrand"}},
"offers": {{"@type": "Offer", "price": "{price}", "priceCurrency": "EUR"}}}}</script>
</head><body><h1>Casque Audio X200</h1><p class="price">{price} €</p></body></html>"""


URL_A = "https://www.site-a.fr/casque-x200"
URL_B = "https://www.site-b.fr/casque-x200"


@pytest.fixture()
def price_drops(db_session, make_project):
    """499 € partout, puis 450 € sur A, puis 430 € sur B."""
    project_id = make_project("Provenance")
    analyze_fetch_result(
        db_session, project_id, make_fetch_result(URL_A, _product_html("499.00")))
    analyze_fetch_result(
        db_session, project_id, make_fetch_result(URL_B, _product_html("499.00")))
    analyze_fetch_result(
        db_session, project_id, make_fetch_result(URL_A, _product_html("450.00")))
    analyze_fetch_result(
        db_session, project_id, make_fetch_result(URL_B, _product_html("430.00")))
    entity = (
        db_session.query(Entity)
        .filter(Entity.project_id == project_id, Entity.entity_type == "product")
        .one()
    )
    return project_id, entity.id


def test_historique_attribue_a_la_bonne_source(price_drops, db_session):
    _project_id, entity_id = price_drops
    changes = (
        db_session.query(Observation)
        .filter(Observation.entity_id == entity_id,
                Observation.field == "price.amount")
        .order_by(Observation.observed_at, Observation.id)
        .all()
    )
    assert len(changes) == 2, "deux baisses de prix attendues dans le journal"
    first, second = changes
    # Baisse sur A : 499 -> 450.
    assert first.old_value == {"value": 499.0}
    assert first.new_value == {"value": 450.0}
    assert first.source_domain == "www.site-a.fr"
    assert first.source_url == URL_A
    # Baisse sur B : 499 -> 430.
    assert second.old_value == {"value": 499.0}
    assert second.new_value == {"value": 430.0}
    assert second.source_domain == "www.site-b.fr"
    assert second.source_url == URL_B
    # Les FK pointent vers les bonnes lignes `sources`.
    sources = {s.canonical_host: s for s in db_session.query(Source).all()}
    assert first.source_id == sources["www.site-a.fr"].id
    assert second.source_id == sources["www.site-b.fr"].id


def test_api_history_expose_la_source(client, db_session, price_drops):
    _project_id, entity_id = price_drops
    response = client.get(f"/api/entities/{entity_id}/history")
    assert response.status_code == 200
    changes = [o for o in response.json() if o["field"] == "price.amount"]
    assert len(changes) == 2
    by_amount = {c["new_value"]["value"]: c for c in changes}
    assert by_amount[450.0]["source_domain"] == "www.site-a.fr"
    assert by_amount[450.0]["source_url"] == URL_A
    assert by_amount[450.0]["source_id"] is not None
    assert by_amount[450.0]["source_name"] == "www.site-a.fr"
    assert by_amount[430.0]["source_domain"] == "www.site-b.fr"
    assert by_amount[430.0]["source_url"] == URL_B
