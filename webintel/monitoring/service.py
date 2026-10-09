"""Service de surveillance : collecte -> pipeline canonique -> règles -> alertes.

`run_monitor(monitor_id, fetcher=None)` :
1. selon `target_type`, détermine la ou les URL à surveiller
   (`domain` -> page d'accueil ; le sitemap complet est reporté en v2) ;
2. fetch (injecté ou `HybridFetcher`), puis **pipeline canonique unique**
   (`core.pipeline.analyze_fetch_result`) : PARSE -> CLASSIFY -> EXTRACT ->
   NORMALIZE -> ENRICH -> ENTITY -> SNAPSHOT -> DIFF -> EVENT -> OBSERVE.
   Il n'y a PLUS de pipeline d'extraction local : une page surveillée
   produit exactement les mêmes entités, champs et événements qu'une page
   analysée via `POST /api/analyze` (P0-2) ;
3. détection du mot-clé éventuel (`config["keyword"]`, spécifique au monitoring) ;
4. évaluation des règles (`monitor.config["rules"]`) -> `alerts.dispatch` ;
5. `monitor.last_run_at` mis à jour.

Chaque événement : type, entity_id, url, before/after, importance, evidence,
created_at.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from webintel.core.enums import EventType
from webintel.core.exceptions import FetchError, SecurityError
from webintel.core.logging import get_logger
from webintel.core.pipeline import analyze_fetch_result
from webintel.core.utils import utcnow
from webintel.crawler.fetcher import HybridFetcher
from webintel.database.models import Event, Monitor
from webintel.monitoring.rules import evaluate_rule

logger = get_logger(__name__)

# Type d'entité prioritaire par type de page (miroir de
# `core.pipeline._primary_entity_kind`, dupliqué ici pour le contexte des règles).
_PRIMARY_ENTITY_KIND = {"product": "product", "article": "article",
                        "blog": "article", "news": "article", "company": "company"}


def _target_urls(monitor: Monitor) -> tuple[list[str], str | None]:
    """URLs à surveiller selon le `target_type` (+ mot-clé éventuel).

    - url / product / price : la cible est une URL produit/page ;
    - domain : la page d'accueil (le parcours complet du sitemap est
      reporté en v2 — documenté ici plutôt qu'implémenté à moitié) ;
    - keyword : la cible est une URL, le mot-clé est dans `config["keyword"]`.
    """
    target_type = (monitor.target_type or "url").lower()
    config = monitor.config or {}
    if target_type == "keyword":
        return [monitor.target], (config.get("keyword") or "")
    return [monitor.target], None


def _primary_entity_data(fiche: dict) -> dict:
    """Données de l'entité prioritaire d'une fiche (pour le contexte des règles)."""
    primary_kind = _PRIMARY_ENTITY_KIND.get(str(fiche.get("page_type") or ""), "webpage")
    for entity in fiche.get("entities") or []:
        if isinstance(entity, dict) and entity.get("entity_type") == primary_kind:
            return dict(entity.get("data") or {})
    return {}


# ---------------------------------------------------------------------------
# Exécution d'un moniteur
# ---------------------------------------------------------------------------

def run_monitor(
    session: Session,
    monitor_id: int,
    fetcher: HybridFetcher | None = None,
) -> dict:
    """Exécute un moniteur via le pipeline canonique, puis règles -> alertes.

    Retourne `{monitor_id, urls_checked, events: [...], alerts: [...]}`.
    Les événements sont déjà persistés par le pipeline ; seuls les événements
    spécifiques au monitoring (mot-clé) sont créés ici.
    """
    from webintel.alerts.dispatcher import dispatch  # import local : évite tout cycle

    monitor = session.get(Monitor, monitor_id)
    if monitor is None:
        raise ValueError(f"Moniteur introuvable : id={monitor_id}.")
    http = fetcher or HybridFetcher()

    urls, keyword = _target_urls(monitor)
    collected_events: list[dict] = []
    created_alerts: list[dict] = []
    checked = 0
    errors: list[str] = []

    for url in urls:
        try:
            fetch_result = http.fetch(url)
        except Exception as exc:
            errors.append(f"{url} : {exc}")
            logger.warning("Monitor %s : échec de collecte de %s (%s)", monitor.id, url, exc)
            continue
        if fetch_result.error:
            errors.append(f"{url} : {fetch_result.error}")
            logger.warning("Monitor %s : %s -> %s", monitor.id, url, fetch_result.error)
            continue
        checked += 1

        # --- Pipeline canonique (P0-2) : plus de duplication locale. -------
        try:
            fiche = analyze_fetch_result(
                session, monitor.project_id, fetch_result,
                keep_html=bool((monitor.config or {}).get("keep_html", False)),
            )
        except (FetchError, SecurityError) as exc:
            errors.append(f"{url} : {exc}")
            logger.warning("Monitor %s : pipeline en échec pour %s (%s)",
                           monitor.id, url, exc)
            continue

        final_url = fiche.get("final_url") or url
        diff_d = fiche.get("diff") or {}
        for event in fiche.get("events") or []:
            collected_events.append(dict(event))

        # --- Mot-clé : spécifique au monitoring. ---------------------------
        if keyword:
            found = keyword.lower() in (fetch_result.text or "").lower()
            if found:
                row = Event(
                    project_id=monitor.project_id,
                    type=EventType.PAGE_CHANGED.value,
                    entity_id=(collected_events[0].get("entity_id")
                               if collected_events else None),
                    url=final_url,
                    before={"keyword": keyword, "found": False},
                    after={"keyword": keyword, "found": True},
                    importance="medium",
                    evidence={"diff_summary": f"Mot-clé « {keyword} » détecté.",
                               "snapshot_ids": [fiche.get("snapshot_id")]},
                )
                session.add(row)
                session.flush()
                collected_events.append({
                    "id": row.id,
                    "type": row.type,
                    "entity_id": row.entity_id,
                    "url": row.url,
                    "before": row.before,
                    "after": row.after,
                    "importance": row.importance,
                    "evidence": row.evidence,
                })

        # --- Règles du moniteur -> alertes. --------------------------------
        entity_data = _primary_entity_data(fiche)
        context = {"entity": entity_data, "diff": diff_d}
        for event in list(collected_events):
            context["event"] = event
            for rule in (monitor.config or {}).get("rules") or []:
                try:
                    matched = evaluate_rule(rule, context)
                except Exception as exc:
                    logger.warning("Monitor %s : règle invalide ignorée (%s)", monitor.id, exc)
                    continue
                if matched:
                    channel = (rule.get("channel") or "log").lower()
                    alert = dispatch(
                        session, monitor.project_id, channel, rule,
                        payload={"monitor": monitor.name, "monitor_id": monitor.id,
                                 "event": event, "url": final_url},
                        monitor_id=monitor.id,
                    )
                    created_alerts.append({"id": alert.id, "channel": channel,
                                           "status": alert.status})

    monitor.last_run_at = utcnow()
    session.flush()
    if errors:
        logger.info("Monitor %s terminé avec %d erreur(s) : %s", monitor.id, len(errors), "; ".join(errors[:3]))
    return {
        "monitor_id": monitor.id,
        "urls_checked": checked,
        "events": collected_events,
        "alerts": created_alerts,
    }
