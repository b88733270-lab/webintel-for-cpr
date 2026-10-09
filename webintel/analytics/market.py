"""Analyse de marché (P0-3 : observations par source).

- `price_distribution(prices)` : statistiques descriptives + buckets automatiques ;
- `find_same_products(session, project_id)` : regroupe les entités produit par
  clé stable (même produit vu sur plusieurs sources -> une clé, N observations
  par source) ;
- `compare_product_prices(session, project_id)` : pour chaque produit, prix
  par source + distribution (min/max/médiane/moyenne).
"""

from __future__ import annotations

import statistics

from sqlalchemy.orm import Session

from webintel.core.logging import get_logger
from webintel.database.models import Entity, SourceObservation

logger = get_logger(__name__)


def price_distribution(prices: list[float]) -> dict:
    """Distribution d'une liste de prix : buckets, min, max, médiane, moyenne.

    Buckets automatiques simples : jusqu'à 10 intervalles de largeur égale
    sur [min, max]. Chaque bucket : `{range: "a - b", count: n}`.
    """
    clean = sorted(p for p in prices if isinstance(p, (int, float)) and p is not None)
    if not clean:
        return {"buckets": [], "min": None, "max": None, "median": None, "mean": None,
                "count": 0}
    minimum, maximum = clean[0], clean[-1]
    mean = round(statistics.fmean(clean), 2)
    median = round(statistics.median(clean), 2)

    if minimum == maximum:
        buckets = [{"range": f"{minimum:.2f}", "count": len(clean)}]
    else:
        n_buckets = min(10, max(2, len(clean) // 2))
        width = (maximum - minimum) / n_buckets
        counts = [0] * n_buckets
        for value in clean:
            idx = min(int((value - minimum) / width), n_buckets - 1)
            counts[idx] += 1
        buckets = []
        for i, count in enumerate(counts):
            low = minimum + i * width
            high = minimum + (i + 1) * width
            buckets.append({"range": f"{low:.2f} - {high:.2f}", "count": count})
    return {
        "buckets": buckets,
        "min": minimum,
        "max": maximum,
        "median": median,
        "mean": mean,
        "count": len(clean),
    }


def _latest_price_observations(
    session: Session, entity_ids: list[int]
) -> dict[tuple[int, str], SourceObservation]:
    """Dernière observation `field="price"` par (entité, domaine source)."""
    if not entity_ids:
        return {}
    rows = (
        session.query(SourceObservation)
        .filter(
            SourceObservation.entity_id.in_(entity_ids),
            SourceObservation.field == "price",
        )
        .order_by(
            SourceObservation.entity_id,
            SourceObservation.observed_at.desc(),
            SourceObservation.id.desc(),
        )
        .all()
    )
    latest: dict[tuple[int, str], SourceObservation] = {}
    for row in rows:
        key = (row.entity_id, row.source_domain or "")
        if key not in latest:
            latest[key] = row
    return latest


def find_same_products(session: Session, project_id: int) -> list[dict]:
    """Regroupe les produits par clé stable pour la comparaison multi-sources.

    La clé d'entité produit (`product:` + hash marque|nom) est déjà identique
    pour un même produit vu sur 50 sites : chaque groupe est donc une entité
    unique + ses observations **par source** (P0-3). Le prix affiché pour une
    source est la DERNIÈRE valeur observée sur cette source — plus la valeur
    globale fusionnée.

    Retourne :

    ```
    [{key, name,
      observations: [{source_url, source_domain,
                      price: {amount, currency}, observed_at}]}]
    ```
    """
    products = (
        session.query(Entity)
        .filter(Entity.project_id == project_id, Entity.entity_type == "product")
        .order_by(Entity.name)
        .all()
    )
    latest = _latest_price_observations(session, [e.id for e in products])
    groups: list[dict] = []
    for entity in products:
        observations = []
        for (entity_id, domain), obs in sorted(latest.items(), key=lambda kv: kv[0][1]):
            if entity_id != entity.id:
                continue
            value = obs.value if isinstance(obs.value, dict) else {}
            observations.append({
                "source_url": obs.source_url,
                "source_domain": domain,
                "price": {
                    "amount": value.get("amount"),
                    "currency": value.get("currency"),
                },
                "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
            })
        groups.append({
            "key": entity.entity_key,
            "name": entity.name,
            "observations": observations,
        })
    logger.debug("Comparaison multi-sources : %d produit(s) pour le projet %s.",
                 len(groups), project_id)
    return groups


def compare_product_prices(session: Session, project_id: int) -> list[dict]:
    """Comparaison de prix par produit : prix par source + distribution.

    Pour chaque entité produit : `{key, name, prices: [{source_domain,
    source_url, amount, currency, observed_at}], distribution}` où
    `distribution` = `price_distribution` sur les montants (buckets, min, max,
    médiane, moyenne, count). Les produits sans aucun prix observé sont
    exclus.
    """
    comparisons: list[dict] = []
    for group in find_same_products(session, project_id):
        prices = []
        for obs in group["observations"]:
            amount = (obs.get("price") or {}).get("amount")
            if isinstance(amount, (int, float)):
                prices.append({
                    "source_domain": obs.get("source_domain"),
                    "source_url": obs.get("source_url"),
                    "amount": float(amount),
                    "currency": (obs.get("price") or {}).get("currency"),
                    "observed_at": obs.get("observed_at"),
                })
        if not prices:
            continue
        comparisons.append({
            "key": group["key"],
            "name": group["name"],
            "prices": sorted(prices, key=lambda p: p["amount"]),
            "distribution": price_distribution([p["amount"] for p in prices]),
        })
    return comparisons
