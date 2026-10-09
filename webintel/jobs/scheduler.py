"""Planificateur : thread démon qui soumet les moniteurs dus (contrat §7).

À chaque tick (`interval_s`, défaut 60 s) : les moniteurs `active=True` dont
`last_run_at` est nul ou plus ancien que `interval_minutes` sont soumis via
`JobManager.submit("monitor_run", ...)` avec le runner `_run_monitor_job`
(qui appelle `monitoring.service.run_monitor`).

Une erreur sur un moniteur ne fait jamais crasher la boucle (log + suite).
Démarrage prévu dans le lifespan FastAPI si `settings.ENABLE_SCHEDULER`
(lot api). En v2, ce rôle reviendra à Celery beat.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from webintel.core.logging import get_logger
from webintel.database.base import SessionLocal
from webintel.database.models import CrawlJob, Monitor
from webintel.jobs.manager import JobContext, get_manager

logger = get_logger(__name__)


def _run_monitor_job(context: JobContext, session: Session) -> None:
    """Runner du job "monitor_run" : lit `monitor_id` dans les params du job."""
    from webintel.monitoring.service import run_monitor  # import local : évite tout cycle

    job = session.get(CrawlJob, context.job_id)
    monitor_id = (job.params or {}).get("monitor_id") if job else None
    if monitor_id is None:
        raise ValueError(f"Job {context.job_id} : param 'monitor_id' manquant.")
    context.update_progress(monitor_id=monitor_id, phase="collecte")
    result = run_monitor(session, int(monitor_id))
    # Committe avant la mise à jour de progression (qui utilise sa propre
    # session) pour ne pas garder de verrou d'écriture SQLite ouvert.
    session.commit()
    context.update_progress(
        phase="terminé",
        urls_checked=result.get("urls_checked", 0),
        events=len(result.get("events", [])),
        alerts=len(result.get("alerts", [])),
    )


class Scheduler:
    """Boucle de planification des moniteurs (thread démon)."""

    def __init__(self, interval_s: int = 60) -> None:
        self.interval_s = max(1, int(interval_s))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Démarre le thread démon (sans effet s'il tourne déjà)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="webintel-scheduler", daemon=True
        )
        self._thread.start()
        logger.info("Planificateur démarré (tick toutes les %s s).", self.interval_s)

    def stop(self) -> None:
        """Arrête le thread (attente bornée)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s + 5)
            self._thread = None
        logger.info("Planificateur arrêté.")

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self._tick()
            except Exception:
                logger.exception("Planificateur : erreur inattendue pendant le tick.")

    def _tick(self) -> None:
        """Soumet un job `monitor_run` pour chaque moniteur dû.

        Purge aussi les sessions du wizard d'étude expirées (TTL 30 j,
        ``webintel.ui.chercheur_etudes_routes.purger_sessions_expirees``)
        — les études finalisées vivent déjà en base, seuls les brouillons
        expirés sont supprimés.
        """
        session = SessionLocal()
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)  # DB : DateTime naïf
            try:
                # Import différé : évite tout cycle jobs ↔ ui.
                from webintel.ui.chercheur_etudes_routes import (
                    purger_sessions_expirees,
                )
                purgees = purger_sessions_expirees(session)
                if purgees:
                    logger.info(
                        "Planificateur : %d session(s) wizard expirée(s) purgée(s).",
                        purgees,
                    )
            except Exception:
                logger.exception(
                    "Planificateur : purge des sessions wizard impossible.")
            monitors = session.query(Monitor).filter(Monitor.active.is_(True)).all()
            for monitor in monitors:
                try:
                    due = monitor.last_run_at is None or (
                        (now - monitor.last_run_at).total_seconds() / 60
                        >= monitor.interval_minutes
                    )
                except Exception:
                    due = True
                if not due:
                    continue
                try:
                    get_manager().submit(
                        "monitor_run",
                        monitor.project_id,
                        {"monitor_id": monitor.id},
                        runner=_run_monitor_job,
                    )
                    logger.info(
                        "Planificateur : moniteur %s (%s) soumis.",
                        monitor.id, monitor.name,
                    )
                except Exception:
                    logger.exception(
                        "Planificateur : impossible de soumettre le moniteur %s.", monitor.id
                    )
        finally:
            session.close()
