"""Tests des observations métier par source (P0-3).

Avant le correctif, `Entity.data` fusionnait les valeurs de plusieurs
sources : le prix « global » écrasait les prix réellement observés par
boutique. Désormais, `SourceObservation` conserve UNE valeur par
`(entité, domaine source, champ)`.

Scénario : le même produit (même nom/marque -> même `entity_key`) sur
trois boutiques, prix 499 € / 450 € / 430 €.
"""

import threading
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import OperationalError

from webintel.analytics.market import compare_product_prices, find_same_products
from webintel.core.pipeline import analyze_fetch_result
from webintel.core.utils import utcnow
from webintel.database.base import SessionLocal
from webintel.database.helpers import upsert_source_observation
from webintel.database.models import Entity, Project, SourceObservation
from webintel.entities.registry import (
    get_observations_by_source,
    record_source_observations,
    upsert_entity,
)
from webintel.entities.sources import get_or_create_source

from tests.helpers import make_fetch_result


def _product_html(price: str) -> str:
    return f"""<!DOCTYPE html><html lang="fr"><head>
<title>Casque Audio X200</title>
<script type="application/ld+json">{{
"@context": "https://schema.org", "@type": "Product",
"name": "Casque Audio X200",
"brand": {{"@type": "Brand", "name": "SonicBrand"}},
"sku": "X200-BLK",
"offers": {{"@type": "Offer", "price": "{price}", "priceCurrency": "EUR",
           "availability": "https://schema.org/InStock"}}}}
</script></head>
<body><h1>Casque Audio X200</h1><p class="price">{price} €</p></body></html>"""


SOURCES = [
    ("https://www.site-a.fr/casque-x200", "499.00", "www.site-a.fr"),
    ("https://www.site-b.fr/casque-x200", "450.00", "www.site-b.fr"),
    ("https://www.site-c.fr/casque-x200", "430.00", "www.site-c.fr"),
]


@pytest.fixture()
def three_sources(db_session):
    """Projet + le même produit analysé sur 3 sources."""
    project = Project(name="Comparateur")
    db_session.add(project)
    db_session.commit()
    for url, price, _domain in SOURCES:
        analyze_fetch_result(
            db_session, project.id, make_fetch_result(url, _product_html(price))
        )
    return project.id


def test_une_entite_trois_observations_prix(three_sources, db_session):
    """Même produit -> UNE entité, mais TROIS observations `price` (une/source)."""
    entities = (
        db_session.query(Entity)
        .filter(Entity.project_id == three_sources,
                Entity.entity_type == "product")
        .all()
    )
    assert len(entities) == 1
    entity = entities[0]

    prices = (
        db_session.query(SourceObservation)
        .filter(SourceObservation.entity_id == entity.id,
                SourceObservation.field == "price")
        .all()
    )
    assert len(prices) == 3
    by_domain = {o.source_domain: (o.value or {}).get("amount") for o in prices}
    assert by_domain == {
        "www.site-a.fr": 499.0,
        "www.site-b.fr": 450.0,
        "www.site-c.fr": 430.0,
    }


def test_pas_de_doublon_si_prix_inchange(three_sources, db_session):
    """Ré-analyser le même prix sur la même source : aucune ligne ajoutée."""
    before = db_session.query(SourceObservation).count()
    url, price, _domain = SOURCES[0]
    analyze_fetch_result(
        db_session, three_sources, make_fetch_result(url, _product_html(price))
    )
    assert db_session.query(SourceObservation).count() == before


def test_nouvelle_observation_si_prix_change(three_sources, db_session):
    """Un prix modifié sur une source ajoute UNE observation (historique)."""
    url, _old, domain = SOURCES[1]
    analyze_fetch_result(
        db_session, three_sources, make_fetch_result(url, _product_html("439.00"))
    )
    entity = (
        db_session.query(Entity)
        .filter(Entity.project_id == three_sources,
                Entity.entity_type == "product")
        .one()
    )
    latest = (
        db_session.query(SourceObservation)
        .filter(SourceObservation.entity_id == entity.id,
                SourceObservation.field == "price",
                SourceObservation.source_domain == domain)
        .order_by(SourceObservation.observed_at.desc())
        .first()
    )
    assert latest.value["amount"] == 439.0


def test_find_same_products_prix_par_source(three_sources, db_session):
    """`find_same_products` utilise les prix réellement observés par source."""
    matches = find_same_products(db_session, three_sources)
    assert len(matches) == 1
    observations = matches[0]["observations"]
    assert len(observations) == 3
    amounts = sorted(o["price"]["amount"] for o in observations)
    assert amounts == [430.0, 450.0, 499.0]
    assert {o["source_domain"] for o in observations} == {"www.site-a.fr", "www.site-b.fr", "www.site-c.fr"}


def test_compare_product_prices_distribution(three_sources, db_session):
    """`compare_product_prices` : min/max/moyenne/médiane corrects."""
    rows = compare_product_prices(db_session, three_sources)
    assert len(rows) == 1
    row = rows[0]
    assert [p["amount"] for p in row["prices"]] == [430.0, 450.0, 499.0]
    dist = row["distribution"]
    assert dist["min"] == 430.0
    assert dist["max"] == 499.0
    assert dist["mean"] == pytest.approx(459.67, abs=0.01)
    assert dist["median"] == 450.0
    assert dist["count"] == 3


def test_api_expose_observations_par_source(client, db_session, make_project):
    """`GET /api/entities/{id}` groupe les observations par domaine (P0-3)."""
    project_id = make_project("API obs")
    for url, price, _domain in SOURCES:
        analyze_fetch_result(
            db_session, project_id, make_fetch_result(url, _product_html(price))
        )
    entity = (
        db_session.query(Entity)
        .filter(Entity.project_id == project_id, Entity.entity_type == "product")
        .one()
    )
    response = client.get(f"/api/entities/{entity.id}")
    assert response.status_code == 200
    grouped = response.json()["observations_by_source"]
    assert set(grouped) == {"www.site-a.fr", "www.site-b.fr", "www.site-c.fr"}
    amounts = {d: next(o["value"]["amount"] for o in obs if o["field"] == "price")
               for d, obs in grouped.items()}
    assert amounts == {
        "www.site-a.fr": 499.0, "www.site-b.fr": 450.0, "www.site-c.fr": 430.0,
    }

    # Accès direct au registre : même regroupement.
    assert set(get_observations_by_source(db_session, entity.id)) == set(grouped)


# ---------------------------------------------------------------------------
# Chantier 0.2 — vrai upsert : la ré-observation rafraîchit `observed_at`
# ---------------------------------------------------------------------------

def _naive(dt):
    """Datetime sans fuseau pour comparaison (SQLite naïf / PG tz-aware)."""
    return dt.replace(tzinfo=None) if dt is not None else None


def test_reobservation_refresh_50_60_50(db_session):
    """50€ (J1) -> 60€ (J2) -> 50€ (J3) : 2 lignes, `observed_at` du 50€ = J3.

    Régression chantier 0.2 : avec `ON CONFLICT DO NOTHING`, la
    ré-observation du 50€ en J3 était ignorée (`observed_at` restait à J1
    et l'observation affichée devenait fausse). Le vrai upsert rafraîchit
    la date de dernière observation, comme l'exige la docstring du
    modèle `SourceObservation`. Passe par `record_source_observations()`
    (l'API publique) avec des `observed_at` explicites.
    """
    project = Project(name="Réobservation")
    db_session.add(project)
    db_session.commit()
    url = "https://www.reobs.fr/produit"
    entity = upsert_entity(
        db_session, project.id, "product",
        {"name": "Produit Réobs", "brand": "MarqueTest"}, url,
    )
    source = get_or_create_source(db_session, url)
    db_session.commit()

    j1 = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
    j2 = datetime(2026, 1, 11, 12, 0, tzinfo=timezone.utc)
    j3 = datetime(2026, 1, 12, 12, 0, tzinfo=timezone.utc)

    def _data(amount):
        return {
            "name": "Produit Réobs",
            "brand": "MarqueTest",
            "price": {"amount": amount, "currency": "EUR"},
        }

    n1 = record_source_observations(
        db_session, entity, "product", _data(50.0), url,
        source=source, observed_at=j1,
    )
    n2 = record_source_observations(
        db_session, entity, "product", _data(60.0), url,
        source=source, observed_at=j2,
    )
    n3 = record_source_observations(
        db_session, entity, "product", _data(50.0), url,
        source=source, observed_at=j3,
    )
    db_session.commit()

    prices = (
        db_session.query(SourceObservation)
        .filter(
            SourceObservation.entity_id == entity.id,
            SourceObservation.source_id == source.id,
            SourceObservation.field == "price",
        )
        .all()
    )
    assert len(prices) == 2, "50€ puis 60€ puis 50€ : exactement 2 lignes"
    by_amount = {(o.value or {}).get("amount"): o for o in prices}
    assert set(by_amount) == {50.0, 60.0}
    # La ré-observation du 50€ a rafraîchi `observed_at` (pas ignorée).
    assert _naive(by_amount[50.0].observed_at) == _naive(j3)
    assert _naive(by_amount[60.0].observed_at) == _naive(j2)
    # Compteur : J1 -> 4 nouvelles valeurs (price, availability, name,
    # brand), J2 -> 1 (le price 60€), J3 -> 0 (le 50€ est rafraîchi,
    # pas recompté comme nouveau).
    assert (n1, n2, n3) == (4, 1, 0)


def _upsert_avec_retry(fn, attempts=40):
    """Exécute `fn()` en réessayant uniquement sur `database is locked`.

    SQLite multi-threads : un verrou passager est normal sous contention
    (rollback + attente courte, backoff linéaire). Toute autre exception
    remonte immédiatement pour ne pas masquer les vrais échecs ; si le
    verrou persiste après `attempts` essais, l'erreur remonte aussi.
    """
    last = None
    for attempt in range(attempts):
        try:
            return fn()
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last = exc
            time.sleep(0.02 * (attempt + 1))
    raise last  # type: ignore[misc]


def test_upsert_concurrent_same_observation(db_session):
    """8 threads x même observation simultanée -> EXACTEMENT 1 ligne.

    Base SQLite fichier partagée (conftest, pas `:memory:`) +
    `check_same_thread=False` (base.py) ; chaque thread a sa propre
    session. Les `database is locked` éventuels sont réessayés via
    `_upsert_avec_retry`, sans masquer les vrais échecs.
    """
    project = Project(name="Concurrence upsert")
    db_session.add(project)
    db_session.commit()
    project_id = project.id
    url = "https://www.conc-upsert.fr/x"
    t0 = utcnow()
    errors: list = []
    barrier = threading.Barrier(8)

    def target():
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)

            def _work():
                entity = upsert_entity(
                    session, project_id, "product",
                    {"name": "Produit ConcUpsert"}, url,
                )
                source = get_or_create_source(session, url)
                session.flush()
                upsert_source_observation(
                    session,
                    entity_id=entity.id,
                    source_id=source.id,
                    field="price",
                    value={"amount": 100.0, "currency": "EUR"},
                    source_url=url,
                    source_domain=source.canonical_host,
                    observed_at=utcnow(),
                )
                session.commit()

            def _attempt():
                try:
                    _work()
                except OperationalError:
                    session.rollback()
                    raise

            _upsert_avec_retry(_attempt)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=target) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == [], f"erreurs de threads : {errors!r}"

    session = SessionLocal()
    try:
        entity = (
            session.query(Entity)
            .filter(Entity.project_id == project_id,
                    Entity.entity_type == "product")
            .one()
        )
        rows = (
            session.query(SourceObservation)
            .filter(
                SourceObservation.entity_id == entity.id,
                SourceObservation.field == "price",
            )
            .all()
        )
        assert len(rows) == 1, "8 threads x même observation : 1 seule ligne"
        row = rows[0]
        assert row.value == {"amount": 100.0, "currency": "EUR"}
        assert row.observed_at is not None
        assert _naive(row.observed_at) >= _naive(t0), "observed_at à jour"
    finally:
        session.close()


def test_value_hash_distingue_valeurs_et_refresh_sans_doublon(db_session):
    """50€ vs 60€ : 2 lignes (`value_hash` distincts) ; ré-observer le 50€
    ne crée pas de 3e ligne mais rafraîchit `observed_at`/`confidence`.

    Exerce `upsert_source_observation()` directement (contrat du helper) :
    `(objet, True)` à l'insertion, `(objet, False)` à la ré-observation.
    """
    project = Project(name="ValueHash")
    db_session.add(project)
    db_session.commit()
    url = "https://www.vhash.fr/p"
    entity = upsert_entity(
        db_session, project.id, "product", {"name": "Produit VH"}, url
    )
    source = get_or_create_source(db_session, url)
    db_session.commit()

    base = dict(
        entity_id=entity.id,
        source_id=source.id,
        field="price",
        source_url=url,
        source_domain=source.canonical_host,
    )
    o50, created50 = upsert_source_observation(
        db_session, value={"amount": 50.0, "currency": "EUR"},
        confidence=0.5, **base,
    )
    o60, created60 = upsert_source_observation(
        db_session, value={"amount": 60.0, "currency": "EUR"},
        confidence=0.5, **base,
    )
    assert created50 is True
    assert created60 is True
    assert o50.value_hash != o60.value_hash

    # Ré-observation du 50€ : pas de 3e ligne, métadonnées rafraîchies.
    plus_tard = datetime(2026, 5, 1, 8, 30, tzinfo=timezone.utc)
    o50_bis, created_bis = upsert_source_observation(
        db_session, value={"amount": 50.0, "currency": "EUR"},
        confidence=0.9, observed_at=plus_tard, **base,
    )
    assert created_bis is False
    assert o50_bis.id == o50.id
    db_session.commit()

    rows = (
        db_session.query(SourceObservation)
        .filter(
            SourceObservation.entity_id == entity.id,
            SourceObservation.source_id == source.id,
            SourceObservation.field == "price",
        )
        .all()
    )
    assert len(rows) == 2
    row50 = next(r for r in rows if (r.value or {}).get("amount") == 50.0)
    assert _naive(row50.observed_at) == _naive(plus_tard)
    assert row50.confidence == pytest.approx(0.9)
