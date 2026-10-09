"""KPIs du tableau de bord (contrat §9 d'architecture.md).

`get_dashboard_kpis(session, project_id=None)` retourne :
- `pages_surveillees` : moniteurs actifs ;
- `changements_aujourdhui` : événements créés aujourd'hui ;
- `produits_detectes` : entités de type produit ;
- `changements_prix_7j` : événements PRICE_DROP / PRICE_INCREASE des 7 derniers jours ;
- `erreurs_24h` : snapshots en erreur (statut >= 400) + jobs FAILED des dernières 24 h ;
- `nouvelles_entites_7j` : entités dont `first_seen` date de moins de 7 jours ;
- `alertes` : total des alertes ;
- `temps_moyen_crawl_s` : durée moyenne des jobs crawl terminés
  (`updated_at - created_at` en secondes).

Toutes les requêtes sont filtrées par `project_id` quand il est fourni.
Les comparaisons de dates utilisent des datetimes naïfs UTC (stockage DB).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from webintel.core.enums import EventType, JobStatus
from webintel.core.utils import utcnow
from webintel.database.models import Alert, CrawlJob, Entity, Event, Monitor, PageSnapshot


def _naive(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _start_of_today() -> datetime:
    return utcnow().replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)


def get_dashboard_kpis(session: Session, project_id: int | None = None) -> dict:
    """Calcule les KPIs du tableau de bord (optionnellement pour un projet)."""
    now = utcnow().replace(tzinfo=None)
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    today = _start_of_today()

    def scoped(query, model):
        if project_id is not None:
            query = query.filter(model.project_id == project_id)
        return query

    pages_surveillees = (
        scoped(session.query(func.count(Monitor.id)), Monitor)
        .filter(Monitor.active.is_(True))
        .scalar() or 0
    )
    changements_aujourdhui = (
        scoped(session.query(func.count(Event.id)), Event)
        .filter(Event.created_at >= today)
        .scalar() or 0
    )
    produits_detectes = (
        scoped(session.query(func.count(Entity.id)), Entity)
        .filter(Entity.entity_type == "product")
        .scalar() or 0
    )
    changements_prix_7j = (
        scoped(session.query(func.count(Event.id)), Event)
        .filter(
            Event.type.in_([EventType.PRICE_DROP.value, EventType.PRICE_INCREASE.value]),
            Event.created_at >= week_ago,
        )
        .scalar() or 0
    )
    snapshots_erreur = (
        scoped(session.query(func.count(PageSnapshot.id)), PageSnapshot)
        .filter(PageSnapshot.status_code >= 400, PageSnapshot.fetched_at >= day_ago)
        .scalar() or 0
    )
    jobs_erreur = (
        scoped(session.query(func.count(CrawlJob.id)), CrawlJob)
        .filter(CrawlJob.status == JobStatus.FAILED.value, CrawlJob.updated_at >= day_ago)
        .scalar() or 0
    )
    nouvelles_entites_7j = (
        scoped(session.query(func.count(Entity.id)), Entity)
        .filter(Entity.first_seen >= week_ago)
        .scalar() or 0
    )
    alertes = scoped(session.query(func.count(Alert.id)), Alert).scalar() or 0

    # Durée moyenne des crawls terminés (Python : pas de soustraction de
    # datetimes portable en SQL sur SQLite).
    crawl_query = (
        scoped(session.query(CrawlJob.created_at, CrawlJob.updated_at), CrawlJob)
        .filter(CrawlJob.kind == "crawl", CrawlJob.status == JobStatus.COMPLETED.value)
        .limit(500)
    )
    durations: list[float] = []
    for created_at, updated_at in crawl_query.all():
        start, end = _naive(created_at), _naive(updated_at)
        if start and end and end >= start:
            durations.append((end - start).total_seconds())
    temps_moyen_crawl_s = round(sum(durations) / len(durations), 1) if durations else None

    return {
        "pages_surveillees": int(pages_surveillees),
        "changements_aujourdhui": int(changements_aujourdhui),
        "produits_detectes": int(produits_detectes),
        "changements_prix_7j": int(changements_prix_7j),
        "erreurs_24h": int(snapshots_erreur + jobs_erreur),
        "nouvelles_entites_7j": int(nouvelles_entites_7j),
        "alertes": int(alertes),
        "temps_moyen_crawl_s": temps_moyen_crawl_s,
    }
