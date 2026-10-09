"""Tests du pipeline canonique unique (P0-2).

Avant le correctif, `webintel/api/pipeline.py` et
`webintel/monitoring/service.py::_default_pipeline()` implémentaient DEUX
chaînes d'analyse différentes. Désormais :
- une seule implémentation vit dans `webintel/core/pipeline.py` ;
- `api/pipeline.py` n'est qu'un shim de compatibilité ;
- le monitoring appelle `core.pipeline.analyze_fetch_result` ;
- le crawl peut brancher chaque page sur ce même pipeline.

Ces tests vérifient qu'analyser et surveiller la MÊME fixture produit les
MÊMES entités et les MÊMES champs riches (contacts, liens, images, prix).
"""

import pytest

from webintel.core import pipeline as core_pipeline
from webintel.api import pipeline as api_pipeline
from webintel.crawler.fetcher import HybridFetcher
from webintel.database.models import Entity, Monitor, Project
from webintel.monitoring import service as monitoring_service
from webintel.monitoring.service import run_monitor

from tests.helpers import make_fetch_result


def _product_html(price: str) -> str:
    return f"""<!DOCTYPE html><html lang="fr"><head>
<title>Casque Audio X200 - Boutique</title>
<meta property="og:title" content="Casque Audio X200">
<script type="application/ld+json">{{
"@context": "https://schema.org", "@type": "Product",
"name": "Casque Audio X200",
"brand": {{"@type": "Brand", "name": "SonicBrand"}},
"sku": "X200-BLK",
"description": "Casque audio sans fil à réduction de bruit.",
"offers": {{"@type": "Offer", "price": "{price}", "priceCurrency": "EUR",
           "availability": "https://schema.org/InStock"}}}}
</script></head>
<body>
<h1>Casque Audio X200</h1>
<p class="price">{price} €</p>
<a href="mailto:contact@boutique.example.com">contact@boutique.example.com</a>
<a href="tel:+33123456789">01 23 45 67 89</a>
<img src="/images/x200.jpg" alt="Casque X200">
<a href="/livraison">Livraison</a>
</body></html>"""


URL = "https://boutique.example.com/casque-x200"
HTML = _product_html("499.00")


def _project(db_session, name: str) -> int:
    p = Project(name=name)
    db_session.add(p)
    db_session.commit()
    return p.id


def _product_entities(db_session, project_id: int):
    return (
        db_session.query(Entity)
        .filter(Entity.project_id == project_id, Entity.entity_type == "product")
        .all()
    )


# ---------------------------------------------------------------------------
# Unicité de l'implémentation
# ---------------------------------------------------------------------------

def test_api_pipeline_est_un_shim_du_noyau():
    """`api.pipeline` réexporte le pipeline canonique (aucune duplication)."""
    assert api_pipeline.analyze_url is core_pipeline.analyze_url
    assert api_pipeline.analyze_fetch_result is core_pipeline.analyze_fetch_result


def test_monitoring_sans_pipeline_duplique():
    """Le monitoring n'a plus son `_default_pipeline` local."""
    assert not hasattr(monitoring_service, "_default_pipeline")


# ---------------------------------------------------------------------------
# Parité analyse / monitoring
# ---------------------------------------------------------------------------

def _rich_fields(db_session, fiche: dict, entity: Entity) -> dict:
    """Champs riches : fiche (contacts/liens/images) + identité entité.

    Discipline v1.1.1 : le prix et la disponibilité ne sont PLUS dans
    `Entity.data` (identité uniquement) — ils sont lus dans les observations
    par source de l'entité.
    """
    from webintel.database.models import SourceObservation

    data = dict(entity.data or {})
    assert "price" not in data, "Entity.data ne doit plus contenir de prix (v1.1.1)"
    assert "availability" not in data, "Entity.data ne doit plus contenir de disponibilité (v1.1.1)"
    obs_values = {}
    for row in (
        db_session.query(SourceObservation)
        .filter(SourceObservation.entity_id == entity.id)
        .all()
    ):
        obs_values.setdefault(row.field, row.value)
    price = obs_values.get("price") or {}
    return {
        "entity_key": entity.entity_key,
        "name": data.get("name"),
        "brand": data.get("brand"),
        "price_amount": price.get("amount"),
        "price_currency": price.get("currency"),
        "availability": obs_values.get("availability"),
        "entity_images": data.get("images"),
        "page_contacts": fiche.get("contacts"),
        "page_links_count": len(fiche.get("links") or []),
        "page_images_count": len(fiche.get("images") or []),
    }


def test_analyze_url_et_analyze_fetch_result_concordent(db_session, monkeypatch):
    """`analyze_url` (fetch+pipeline) == `analyze_fetch_result` (pipeline seul)."""
    p_analyze = _project(db_session, "P analyse")
    p_direct = _project(db_session, "P direct")

    monkeypatch.setattr(
        HybridFetcher, "fetch",
        lambda self, url, **kw: make_fetch_result(url, HTML),
    )
    fiche_url = core_pipeline.analyze_url(db_session, p_analyze, URL)

    fiche_fetch = core_pipeline.analyze_fetch_result(
        db_session, p_direct, make_fetch_result(URL, HTML)
    )

    assert fiche_url["page_type"] == fiche_fetch["page_type"] == "product"
    ent_url = _product_entities(db_session, p_analyze)
    ent_fetch = _product_entities(db_session, p_direct)
    assert len(ent_url) == len(ent_fetch) == 1
    assert _rich_fields(db_session, fiche_url, ent_url[0]) == _rich_fields(db_session, fiche_fetch, ent_fetch[0])
    assert ent_url[0].entity_key == ent_fetch[0].entity_key
    # Champs riches réellement extraits (entité + fiche).
    rich = _rich_fields(db_session, fiche_url, ent_url[0])
    assert rich["name"] == "Casque Audio X200"
    assert rich["brand"] == "SonicBrand"
    assert rich["price_amount"] == 499.0
    assert rich["price_currency"] == "EUR"
    assert rich["availability"] == "in_stock"
    assert rich["entity_images"] == ["https://boutique.example.com/images/x200.jpg"]
    assert rich["page_contacts"]["emails"] == ["contact@boutique.example.com"]
    assert rich["page_images_count"] >= 1


class _FakeFetcher:
    """Simule `HybridFetcher` sans réseau (même `fetch(url)`)."""

    def __init__(self, html: str):
        self._html = html

    def fetch(self, url: str, **kwargs):
        return make_fetch_result(url, self._html)


def test_monitoring_produit_meme_pipeline_que_analyse(db_session):
    """Surveiller la fixture crée la MÊME entité produit que l'analyse directe."""
    p_analyze = _project(db_session, "P analyse/moniteur")
    p_monitor = _project(db_session, "P moniteur")

    core_pipeline.analyze_fetch_result(
        db_session, p_analyze, make_fetch_result(URL, HTML)
    )

    monitor = Monitor(
        project_id=p_monitor, name="Veille X200", target_type="url",
        target=URL, interval_minutes=60, active=True, config={},
    )
    db_session.add(monitor)
    db_session.commit()

    result = run_monitor(db_session, monitor.id, fetcher=_FakeFetcher(HTML))
    assert result["urls_checked"] == 1

    ent_analyze = _product_entities(db_session, p_analyze)
    ent_monitor = _product_entities(db_session, p_monitor)
    assert len(ent_analyze) == len(ent_monitor) == 1
    # Même entité logique (clé stable marque|nom) et mêmes champs riches.
    assert ent_analyze[0].entity_key == ent_monitor[0].entity_key
    data_a, data_m = dict(ent_analyze[0].data or {}), dict(ent_monitor[0].data or {})
    for field in ("name", "brand", "price", "availability", "images"):
        assert data_a.get(field) == data_m.get(field), field
