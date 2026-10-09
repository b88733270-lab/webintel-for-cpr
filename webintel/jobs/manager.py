"""Gestionnaire de jobs en process (contrat §7 d'architecture.md).

`JobManager` (singleton via `get_manager()`) :
- `submit(kind, project_id, params, runner) -> job_id` — `runner` est un
  `Callable[[JobContext, Session], None]` ;
- `status(job_id) -> dict` : `{id, kind, status, progress, error, created_at, updated_at}` ;
- `cancel(job_id)`, `pause(job_id)`, `resume(job_id)` — idempotents, sûrs si
  le job est terminé.

États : QUEUED -> RUNNING -> (PAUSED <-> RUNNING) -> COMPLETED | FAILED | CANCELLED.
Exécution via `ThreadPoolExecutor(max_workers=4)`, persistance en DB
(`crawl_jobs`), session DB dédiée par job.

Interface documentée pour substitution Celery v2 : les méthodes publiques
(`submit`, `status`, `cancel`, `pause`, `resume`) et `JobContext`
(`job_id`, `is_cancelled()`, `is_paused()`, `wait_if_paused()`,
`update_progress(**kwargs)`) gardent les MÊMES noms et signatures ; seul
l'exécuteur interne changerait (worker Celery au lieu du pool de threads).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, ThreadPoolExecutor

from sqlalchemy.orm import Session

from webintel.core.enums import JobStatus
from webintel.core.logging import get_logger
from webintel.database.base import SessionLocal
from webintel.database.models import CrawlJob

logger = get_logger(__name__)

_TERMINAL_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
}


def _avec_retry_sqlite(action: str, fn, tentatives: int = 4):
    """Exécute ``fn()`` en réessayant sur erreur SQLite transitoire.

    Chantier robustesse — les workers et les requêtes HTTP écrivent depuis
    des threads différents ; sous SQLite une écriture concurrente peut
    échouer (« database is locked ») même avec ``PRAGMA busy_timeout``.
    On préfère attendre et réessayer plutôt que perdre une transition
    d'état d'un job (ex. : une annulation qui semblerait acceptée mais ne
    serait jamais persistée).
    """
    derniere: Exception | None = None
    for tentative in range(tentatives):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — on réessaie tout
            derniere = exc
            time.sleep(0.3 * (tentative + 1))
    raise RuntimeError(
        f"{action} impossible après {tentatives} tentatives : {derniere}."
    ) from derniere


class JobContext:
    """Contexte d'exécution passé au `runner` d'un job.

    - `job_id` : identifiant du job en DB ;
    - `is_cancelled()` / `is_paused()` : drapeaux positionnés par
      `JobManager.cancel()` / `pause()` — le runner doit les consulter
      régulièrement dans ses boucles longues ;
    - `wait_if_paused()` : dort par pas de 0,5 s tant que le job est en pause
      (sort aussi si le job est annulé) ;
    - `update_progress(**kwargs)` : fusionne les clés dans
      `crawl_jobs.progress` en DB (ex. `visited=10, remaining=90`).
    """

    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        self._cancelled = threading.Event()
        self._paused = threading.Event()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def wait_if_paused(self) -> None:
        """Bloque par pas de 0,5 s tant que le job est en pause."""
        while self._paused.is_set() and not self._cancelled.is_set():
            time.sleep(0.5)

    def update_progress(self, **kwargs) -> None:
        """Fusionne `kwargs` dans la progression du job en DB.

        Best-effort : réessaie quelques fois en cas de verrou SQLite
        (un job peut écrire pendant qu'un autre committe), puis abandonne
        en loggant plutôt qu'en faisant échouer le runner.
        """
        last_exc: Exception | None = None
        for attempt in range(5):
            session = SessionLocal()
            try:
                job = session.get(CrawlJob, self.job_id)
                if job is None:
                    return
                progress = dict(job.progress or {})
                progress.update(kwargs)
                job.progress = progress  # réassignation : suivi JSON SQLAlchemy
                session.commit()
                return
            except Exception as exc:
                last_exc = exc
                session.rollback()
                time.sleep(0.2 * (attempt + 1))
            finally:
                session.close()
        logger.warning(
            "Job %s : impossible de mettre à jour la progression (%s).",
            self.job_id, last_exc,
        )


class JobManager:
    """Ordonnanceur de jobs en process (remplaçable par Celery en v2)."""

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="webintel-job"
        )
        self._futures: dict[int, object] = {}
        self._contexts: dict[int, JobContext] = {}
        self._lock = threading.Lock()

    # -- soumission / exécution -------------------------------------------
    def submit(
        self,
        kind: str,
        project_id: int,
        params: dict,
        runner: Callable[[JobContext, Session], None],
    ) -> int:
        """Crée le job en DB (QUEUED) et le soumet à l'exécuteur. Retourne `job_id`.

        L'INSERT est réessayé quelques fois en cas de contention SQLite
        (plusieurs workers/threads écrivant en concurrence) — même motif
        que ``JobContext.update_progress`` : on préfère attendre que
        faire échouer la soumission.
        """
        def _inserer() -> int:
            session = SessionLocal()
            try:
                job = CrawlJob(
                    project_id=project_id,
                    kind=kind,
                    status=JobStatus.QUEUED.value,
                    params=dict(params or {}),
                )
                session.add(job)
                session.commit()
                return job.id
            finally:
                session.close()

        job_id = _avec_retry_sqlite("Soumission du job", _inserer)

        context = JobContext(job_id)
        with self._lock:
            self._contexts[job_id] = context
            self._futures[job_id] = self._executor.submit(self._execute, job_id, runner, context)
        logger.info("Job %s soumis (%s, projet %s).", job_id, kind, project_id)
        return job_id

    def _execute(
        self, job_id: int, runner: Callable[[JobContext, Session], None], context: JobContext
    ) -> None:
        """Corps d'exécution : RUNNING -> COMPLETED | FAILED | CANCELLED."""
        session = SessionLocal()
        self._set_status(job_id, JobStatus.RUNNING.value)
        try:
            if context.is_cancelled():
                self._set_status(job_id, JobStatus.CANCELLED.value)
                return
            runner(context, session)
            session.commit()
            final = (
                JobStatus.CANCELLED.value
                if context.is_cancelled()
                else JobStatus.COMPLETED.value
            )
            self._set_status(job_id, final)
            logger.info("Job %s terminé : %s.", job_id, final)
        except CancelledError:
            session.rollback()
            self._set_status(job_id, JobStatus.CANCELLED.value)
            logger.info("Job %s annulé.", job_id)
        except Exception as exc:
            session.rollback()
            self._set_status(job_id, JobStatus.FAILED.value, error=str(exc))
            logger.exception("Job %s en échec : %s", job_id, exc)
        finally:
            session.close()

    def _set_status(self, job_id: int, status: str, error: str | None = None) -> None:
        def _ecrire() -> None:
            session = SessionLocal()
            try:
                job = session.get(CrawlJob, job_id)
                if job is None:
                    return
                job.status = status
                if error is not None:
                    job.error = error
                session.commit()
            finally:
                session.close()

        try:
            _avec_retry_sqlite(f"Statut {status} du job {job_id}", _ecrire)
        except RuntimeError:
            # Contrat historique : ne lève jamais (appelé depuis les
            # workers) — mais ne camoufle plus : log + retry d'abord.
            logger.exception(
                "Job %s : impossible de fixer le statut %s.", job_id, status
            )

    # -- introspection / contrôle ------------------------------------------
    def status(self, job_id: int) -> dict:
        """État courant du job (lève `KeyError` si l'id est inconnu)."""
        session = SessionLocal()
        try:
            job = session.get(CrawlJob, job_id)
            if job is None:
                raise KeyError(f"Job introuvable : id={job_id}.")
            return {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "progress": dict(job.progress or {}),
                "error": job.error,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            }
        finally:
            session.close()

    def _context(self, job_id: int) -> JobContext | None:
        with self._lock:
            return self._contexts.get(job_id)

    def cancel(self, job_id: int) -> None:
        """Annule le job (drapeau + annulation de la future si pas démarrée).

        Idempotent : sans effet si le job est déjà dans un état terminal."""
        context = self._context(job_id)
        if context is not None:
            context._cancelled.set()
            context._paused.clear()  # débloque un éventuel wait_if_paused()
        with self._lock:
            future = self._futures.get(job_id)
        if future is not None:
            try:
                future.cancel()
            except Exception:
                pass
        def _marquer() -> None:
            session = SessionLocal()
            try:
                job = session.get(CrawlJob, job_id)
                if job is not None and job.status not in _TERMINAL_STATUSES:
                    job.status = JobStatus.CANCELLED.value
                    session.commit()
            finally:
                session.close()

        # Lève (→ 500) si la persistance échoue vraiment : une annulation
        # qui semblerait acceptée sans être enregistrée serait pire.
        _avec_retry_sqlite(f"Annulation du job {job_id}", _marquer)

    def pause(self, job_id: int) -> None:
        """Met le job en pause (le runner doit appeler `wait_if_paused()`).

        Sans effet si le job n'est pas en cours d'exécution."""
        context = self._context(job_id)
        if context is None:
            return
        def _marquer() -> None:
            session = SessionLocal()
            try:
                job = session.get(CrawlJob, job_id)
                if job is None or job.status != JobStatus.RUNNING.value:
                    return
                context._paused.set()
                job.status = JobStatus.PAUSED.value
                session.commit()
                logger.info("Job %s en pause.", job_id)
            finally:
                session.close()

        _avec_retry_sqlite(f"Mise en pause du job {job_id}", _marquer)

    def resume(self, job_id: int) -> None:
        """Reprend un job en pause. Sans effet s'il n'est pas en pause."""
        context = self._context(job_id)
        if context is None:
            return
        def _marquer() -> None:
            session = SessionLocal()
            try:
                job = session.get(CrawlJob, job_id)
                if job is None or job.status != JobStatus.PAUSED.value:
                    return
                context._paused.clear()
                job.status = JobStatus.RUNNING.value
                session.commit()
                logger.info("Job %s repris.", job_id)
            finally:
                session.close()

        _avec_retry_sqlite(f"Reprise du job {job_id}", _marquer)

    def shutdown(self, wait: bool = True) -> None:
        """Arrête l'exécuteur (utilisé aux tests / à l'arrêt applicatif)."""
        self._executor.shutdown(wait=wait, cancel_futures=True)


_manager: JobManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> JobManager:
    """Retourne le singleton `JobManager` (créé une seule fois)."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = JobManager()
        return _manager
