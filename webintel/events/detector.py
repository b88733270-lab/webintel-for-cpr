"""WebIntel — détection d'événements métier (contrat §9 d'architecture.md).

`detect_events(diff, entity, url, snapshot_ids)` construit les événements
typés (`EventType`) à partir d'un diff intelligent. Module neutre (aucune
dépendance vers `api/`, `monitoring/` ou `core/pipeline.py`) pour éviter
tout cycle d'imports : il est utilisé par le pipeline canonique
(`core/pipeline.py`) et par le service de monitoring.
"""

from __future__ import annotations

from pydantic import BaseModel

from webintel.core.enums import ChangeLevel, EventType
from webintel.core.utils import utcnow


def _as_dict(diff) -> dict:
    if isinstance(diff, BaseModel):
        return diff.model_dump(mode="json")
    return dict(diff or {})


def detect_events(
    diff,
    entity: dict | None,
    url: str,
    snapshot_ids: list[int] | None = None,
) -> list[dict]:
    """Construit les événements typés (`EventType`) à partir d'un diff.

    - baisse / hausse de `price.amount` -> PRICE_DROP / PRICE_INCREASE (high) ;
    - `availability` -> "in_stock" -> BACK_IN_STOCK (high),
      -> "out_of_stock" -> OUT_OF_STOCK (high) ;
    - pas d'ancien snapshot (entité `None` ou diff marqué `is_first`) ->
      NEW_PRODUCT / NEW_ARTICLE / NEW_COMPANY (medium, selon le type de page) ;
    - PAGE_CHANGED (medium si MINOR, low si COSMETIC) ;
    - SEO_CHANGED / TECHNOLOGY_CHANGED (low).

    Chaque événement : `{type, entity_id, url, before, after, importance,
    evidence{diff_summary, snapshot_ids}, created_at}`. Les événements de prix
    portent aussi `price{amount, currency}`, ceux de dispo `availability`,
    pour l'évaluation des règles.
    """
    diff_d = _as_dict(diff)
    changes = diff_d.get("changes") or []
    entity = entity or {}
    entity_id = entity.get("id")
    page_type = str(diff_d.get("page_type") or entity.get("entity_type") or "webpage")
    evidence = {
        "diff_summary": diff_d.get("summary_fr", ""),
        "snapshot_ids": list(snapshot_ids or []),
    }
    events: list[dict] = []

    def make(event_type: EventType, before, after, importance: str, **extra):
        event = {
            "type": event_type.value,
            "entity_id": entity_id,
            "url": url,
            "before": before,
            "after": after,
            "importance": importance,
            "evidence": evidence,
            "created_at": utcnow().isoformat(),
        }
        event.update(extra)
        return event

    for change in changes:
        field = change.get("field") if isinstance(change, dict) else None
        before = change.get("before") if isinstance(change, dict) else None
        after = change.get("after") if isinstance(change, dict) else None

        if field == "price.amount":
            currency = (diff_d.get("currency") or "")
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                if after < before:
                    events.append(make(EventType.PRICE_DROP, before, after, "high",
                                       price={"amount": after, "currency": currency}))
                elif after > before:
                    events.append(make(EventType.PRICE_INCREASE, before, after, "high",
                                       price={"amount": after, "currency": currency}))
        elif field == "availability":
            if after == "in_stock":
                events.append(make(EventType.BACK_IN_STOCK, before, after, "high",
                                   availability=after))
            elif after == "out_of_stock":
                events.append(make(EventType.OUT_OF_STOCK, before, after, "high",
                                   availability=after))
            else:
                events.append(make(EventType.PAGE_CHANGED, before, after, "medium",
                                   availability=after))
        elif field == "seo_score":
            events.append(make(EventType.SEO_CHANGED, before, after, "low"))
        elif field == "technologies":
            events.append(make(EventType.TECHNOLOGY_CHANGED, before, after, "low"))

    # Première capture : événement de nouveauté selon le type de page.
    if diff_d.get("is_first") or entity_id is None and not changes and not events:
        first_type = {
            "product": EventType.NEW_PRODUCT,
            "article": EventType.NEW_ARTICLE,
            "company": EventType.NEW_COMPANY,
        }.get(page_type, EventType.PAGE_CHANGED)
        events.append(make(first_type, None, {"page_type": page_type}, "medium"))

    # Changement de page générique si aucun événement spécifique ne couvre le diff.
    level = diff_d.get("level")
    content_fields = {"title", "h1", "meta_description", "text", "images", "links", "dom"}
    if any((c.get("field") if isinstance(c, dict) else None) in content_fields for c in changes):
        if level == ChangeLevel.MINOR.value:
            events.append(make(EventType.PAGE_CHANGED, None, None, "medium"))
        elif level == ChangeLevel.COSMETIC.value:
            events.append(make(EventType.PAGE_CHANGED, None, None, "low"))

    return events
