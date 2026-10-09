"""Moteur de recherche d'entités (v1 : filtrage Python sur les entités du projet).

Filtres :
- `q` : recherche insensible à la casse dans le nom + les données sérialisées ;
- `entity_type` : type d'entité (`product`, `article`, `company`, `webpage`) ;
- `min_price` / `max_price` : sur les observations courantes de prix PAR
  SOURCE — une entité matche si AU MOINS UNE de ses dernières observations
  `price` (une par source) est dans l'intervalle (discipline v1.1.1 :
  `Entity.data` ne contient plus les valeurs volatiles) ;
- `domain` : une des sources contient ce domaine ;
- `since` : `last_seen >= since` ;
- `limit` : nombre max de résultats (défaut 50).

Retourne des dicts prêts JSON (`id`, `entity_key`, `entity_type`, `name`,
`prices` — prix courants par source, `domain`, `domains`, `last_seen`,
`sources`...). AUCUN prix global ambigu n'est exposé.

> Recherche plein-texte indexée (FTS) : reportée en v2 — documentée dans
> `docs/roadmap.md` par le lot concerné.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from webintel.core.logging import get_logger
from webintel.database.models import Entity, SourceObservation
from webintel.entities.sources import canonical_host_of

logger = get_logger(__name__)


def _price_amount(value) -> float | None:
    """Montant d'une valeur d'observation `price` ({amount, currency})."""
    amount = value.get("amount") if isinstance(value, dict) else value
    if amount is None:
        return None
    try:
        return float(amount)
    except (TypeError, ValueError):
        return None


def _current_prices_by_entity(
    session: Session, entity_ids: list[int]
) -> dict[int, list[dict]]:
    """Prix courants par entité : dernière observation `price` par source.

    Retourne `{entity_id: [{source_id, source_domain, amount, currency,
    observed_at}, ...]}` trié par montant croissant. Une seule requête.
    """
    if not entity_ids:
        return {}
    rows = (
        session.query(SourceObservation)
        .filter(
            SourceObservation.entity_id.in_(entity_ids),
            SourceObservation.field == "price",
        )
        .order_by(SourceObservation.observed_at.desc(), SourceObservation.id.desc())
        .all()
    )
    latest: dict[tuple[int, str], SourceObservation] = {}
    for row in rows:
        key = (row.entity_id, row.source_domain or "")
        if key not in latest:
            latest[key] = row
    by_entity: dict[int, list[dict]] = {}
    for (entity_id, _domain), row in latest.items():
        amount = _price_amount(row.value)
        if amount is None:
            continue
        currency = (
            (row.value or {}).get("currency") if isinstance(row.value, dict) else None
        )
        by_entity.setdefault(entity_id, []).append({
            "source_id": row.source_id,
            "source_domain": row.source_domain,
            "amount": amount,
            "currency": currency,
            "observed_at": row.observed_at.isoformat() if row.observed_at else None,
        })
    for prices in by_entity.values():
        prices.sort(key=lambda p: p["amount"])
    return by_entity


def _domains_of(sources: list) -> list[str]:
    domains: list[str] = []
    for src in sources or []:
        host = canonical_host_of(str(src))
        if host and host not in domains:
            domains.append(host)
    return domains


def _naive(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def search_entities(
    session: Session,
    project_id: int | None = None,
    q: str | None = None,
    entity_type: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    domain: str | None = None,
    since: datetime | None = None,
    limit: int = 50,
) -> list[dict]:
    """Recherche des entités avec filtres (v1 : filtrage en Python, portable)."""
    query = session.query(Entity).order_by(Entity.last_seen.desc())
    if project_id is not None:
        query = query.filter(Entity.project_id == project_id)
    if entity_type:
        query = query.filter(Entity.entity_type == entity_type.strip().lower())
    if since is not None:
        query = query.filter(Entity.last_seen >= _naive(since))
    # Le filtrage texte/prix/domaine se fait en Python (portable SQLite/PG).
    entities = query.all()

    price_filter = min_price is not None or max_price is not None
    prices_by_entity = _current_prices_by_entity(session, [e.id for e in entities])

    needle = (q or "").strip().lower()
    domain_needle = (domain or "").strip().lower()
    results: list[dict] = []
    for entity in entities:
        data = entity.data or {}
        if needle:
            haystack = f"{entity.name}\n{json.dumps(data, ensure_ascii=False, default=str)}".lower()
            if needle not in haystack:
                continue
        prices = prices_by_entity.get(entity.id, [])
        if price_filter:
            matched = any(
                (min_price is None or p["amount"] >= min_price)
                and (max_price is None or p["amount"] <= max_price)
                for p in prices
            )
            if not matched:
                continue
        domains = _domains_of(entity.sources or [])
        if not domains:
            domains = sorted({p["source_domain"] for p in prices if p["source_domain"]})
        if domain_needle and not any(domain_needle in d for d in domains):
            continue
        results.append({
            "id": entity.id,
            "project_id": entity.project_id,
            "entity_key": entity.entity_key,
            "entity_type": entity.entity_type,
            "name": entity.name,
            # Prix par source (v1.1.1) : AUCUN prix global ambigu.
            "prices": prices,
            "domain": domains[0] if domains else None,
            "domains": domains,
            "sources": entity.sources or [],
            "first_seen": entity.first_seen.isoformat() if entity.first_seen else None,
            "last_seen": entity.last_seen.isoformat() if entity.last_seen else None,
            "history_url": f"/api/entities/{entity.id}/history",
        })
        if len(results) >= max(1, limit):
            break
    logger.debug("Recherche entités : %d résultat(s) (q=%r, type=%r).",
                 len(results), q, entity_type)
    return results
