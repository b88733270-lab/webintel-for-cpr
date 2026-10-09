"""Moteur de crawl borné et coopératif (BFS, déduplication stricte).

`CrawlEngine.run()` explore en largeur depuis `start_urls` avec les bornes
`limits` (max_pages, max_depth, delay_s, concurrency, allow_domains,
exclude_paths, obey_robots). Le crawl est coopératif : un objet `ctx`
(optionnel, fourni par le lot `jobs`) expose `is_cancelled()`,
`is_paused()` et `checkpoint()` ; avec `ctx=None` le crawl tourne sans
interruption. Interface compatible avec un futur worker Celery (v2).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from webintel.config.settings import get_settings
from webintel.core.exceptions import FetchError, SecurityError
from webintel.core.logging import get_logger
from webintel.core.types import FetchResult
from webintel.crawler.fetcher import HybridFetcher
from webintel.crawler.urlutils import extract_links
from webintel.normalization.urls import canonical_url
from webintel.security.robots import RobotChecker
from webintel.security.ssrf import validate_url

logger = get_logger(__name__)


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


class CrawlEngine:
    """Moteur de crawl BFS borné."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def run(
        self,
        job_id: str,
        start_urls: list[str],
        project_id: int,
        limits: dict | None = None,
        progress_cb: Callable[[dict], None] | None = None,
        ctx=None,
        already_visited: set[str] | None = None,
        page_pipeline: Callable[[FetchResult], None] | None = None,
    ) -> dict:
        """Exécute le crawl et retourne un résumé.

        `limits` : max_pages, max_depth, delay_s, concurrency, allow_domains,
        exclude_paths, obey_robots (défauts = settings).
        `ctx` : objet optionnel avec `is_cancelled()`, `is_paused()`,
        `checkpoint()` (fourni par le lot jobs ; `None` = sans interruption).
        `already_visited` : canonical_urls déjà vus (ex. en DB par le lot storage).
        `page_pipeline` : hook optionnel appelé avec chaque `FetchResult`
        récupéré avec succès (dans le thread worker) — typiquement le
        pipeline canonique (`core.pipeline.analyze_fetch_result`) pour que
        chaque page crawlée produise snapshot, entités, diff et événements.
        Une exception du hook est loggée sans interrompre le crawl.
        """
        limits = limits or {}
        max_pages = int(limits.get("max_pages", self._settings.MAX_CRAWL_PAGES))
        max_depth = int(limits.get("max_depth", self._settings.MAX_CRAWL_DEPTH))
        delay_s = float(limits.get("delay_s", self._settings.CRAWL_DELAY_S))
        concurrency = max(1, int(limits.get("concurrency", self._settings.MAX_CONCURRENCY)))
        obey_robots = bool(limits.get("obey_robots", self._settings.OBEY_ROBOTS))
        exclude_paths = list(limits.get("exclude_paths", []) or [])
        allow_domains = limits.get("allow_domains")
        if not allow_domains:
            allow_domains = [_hostname(u) for u in start_urls if _hostname(u)]
        allow_domains = {d.lower() for d in allow_domains if d}

        started = time.monotonic()
        state = {
            "visited": 0, "discovered": 0, "errors": 0, "bytes": 0,
            "urls": [], "pages_since_cb": 0,
        }
        lock = threading.Lock()
        queue: deque[tuple[str, int]] = deque()
        visited: set[str] = set(already_visited or ())

        for raw in start_urls:
            try:
                canon = canonical_url(validate_url(raw))
            except SecurityError as exc:
                logger.warning("start_url bloquée (job %s) : %s", job_id, exc)
                continue
            if canon not in visited:
                visited.add(canon)
                queue.append((canon, 0))

        fetcher = HybridFetcher()
        robots = RobotChecker() if obey_robots else None
        stop = threading.Event()

        def snapshot() -> dict:
            with lock:
                return {
                    "visited": state["visited"], "discovered": state["discovered"],
                    "errors": state["errors"], "bytes": state["bytes"],
                    "elapsed_s": round(time.monotonic() - started, 2),
                    "remaining": len(queue), "urls": list(state["urls"]),
                }

        def maybe_progress(force: bool = False) -> None:
            if progress_cb is None:
                return
            with lock:
                state["pages_since_cb"] += 1
                due = force or state["pages_since_cb"] >= 5
                if due:
                    state["pages_since_cb"] = 0
            if due:
                try:
                    progress_cb(snapshot())
                except Exception:
                    logger.exception("progress_cb a levé une exception (job %s)", job_id)

        def should_stop() -> bool:
            if stop.is_set():
                return True
            if ctx is None:
                return False
            try:
                if ctx.is_cancelled():
                    stop.set()
                    return True
                while ctx.is_paused():
                    time.sleep(0.5)
                    if ctx.is_cancelled():
                        stop.set()
                        return True
                if hasattr(ctx, "checkpoint"):
                    ctx.checkpoint()
            except Exception:
                logger.exception("Erreur du contexte job (job %s)", job_id)
            return stop.is_set()

        def process_one(item: tuple[str, int]) -> list[tuple[str, int]]:
            url, depth = item
            new_items: list[tuple[str, int]] = []
            if should_stop():
                return new_items
            if delay_s > 0:
                time.sleep(delay_s)
            if should_stop():
                return new_items
            # Sécurité + robots avant chaque fetch.
            try:
                validate_url(url)
            except SecurityError as exc:
                logger.warning("URL bloquée pendant le crawl : %s (%s)", url, exc)
                with lock:
                    state["errors"] += 1
                maybe_progress()
                return new_items
            if robots is not None and not robots.can_fetch(url, self._settings.DEFAULT_UA):
                logger.info("URL refusée par robots.txt : %s", url)
                with lock:
                    state["errors"] += 1
                maybe_progress()
                return new_items
            try:
                result: FetchResult = fetcher.fetch(url)
            except (SecurityError, FetchError) as exc:
                logger.warning("Fetch bloqué pour %s : %s", url, exc)
                with lock:
                    state["visited"] += 1
                    state["errors"] += 1
                    state["urls"].append(url)
                maybe_progress()
                return new_items
            with lock:
                state["visited"] += 1
                state["urls"].append(result.final_url or url)
                state["bytes"] += len(result.html.encode("utf-8", errors="ignore"))
                if result.error:
                    state["errors"] += 1
            if result.error is None and result.status_code == 200 and result.html:
                if page_pipeline is not None:
                    try:
                        page_pipeline(result)
                    except Exception:
                        logger.exception(
                            "Pipeline de page en échec pour %s (job %s) : crawl poursuivi.",
                            url, job_id,
                        )
                for link in self._discover_links(result, depth, max_depth, allow_domains, exclude_paths):
                    with lock:
                        if link in visited:
                            continue
                        visited.add(link)
                        state["discovered"] += 1
                    new_items.append((link, depth + 1))
            maybe_progress()
            return new_items

        try:
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="webintel-crawl") as pool:
                while queue and not should_stop():
                    with lock:
                        if state["visited"] >= max_pages:
                            break
                    # Frontière courante (BFS par niveaux), STRICTEMENT bornée à
                    # max_pages : on ne prend jamais plus de pages que le
                    # quota restant (la borne est une garantie, pas un objectif).
                    frontier: list[tuple[str, int]] = []
                    with lock:
                        remaining = max_pages - state["visited"]
                        while queue and len(frontier) < max(1, remaining):
                            frontier.append(queue.popleft())
                    if not frontier:
                        break
                    for new_items in pool.map(process_one, frontier):
                        if should_stop():
                            break
                        for link, depth in new_items:
                            with lock:
                                queue.append((link, depth))
        finally:
            fetcher.close()

        maybe_progress(force=True)
        elapsed = time.monotonic() - started
        logger.info(
            "Crawl terminé (job %s, projet %s) : %d visitées, %d découvertes, %d erreurs en %.1fs",
            job_id, project_id, state["visited"], state["discovered"], state["errors"], elapsed,
        )
        return {
            "visited": state["visited"],
            "discovered": state["discovered"],
            "errors": state["errors"],
            "bytes": state["bytes"],
            "elapsed_s": round(elapsed, 2),
            "urls": state["urls"],
        }

    def _discover_links(
        self,
        result: FetchResult,
        depth: int,
        max_depth: int,
        allow_domains: set[str],
        exclude_paths: list[str],
    ) -> list[str]:
        """Liens découverts depuis une page : filtres domaine/profondeur/exclusions."""
        if depth + 1 > max_depth:
            return []
        ctype = (result.headers.get("content-type") or "").lower()
        if "html" not in ctype:
            # Vérifie quand même le contenu (certains serveurs omettent le type).
            if not result.html.strip().lower().startswith(("<!doctype html", "<html")):
                return []
        discovered: list[str] = []
        try:
            for link in extract_links(result.html, result.final_url):
                target = link["url"]
                host = _hostname(target)
                if allow_domains and host not in allow_domains:
                    continue
                path = urlparse(target).path or "/"
                if any(path.startswith(prefix) for prefix in exclude_paths):
                    continue
                try:
                    discovered.append(canonical_url(validate_url(target)))
                except SecurityError:
                    continue  # lien dangereux : ignoré silencieusement
        except Exception:
            logger.exception("Échec de découverte de liens sur %s", result.final_url)
        return discovered


# Alias documenté : l'API REST (lot api) pourra appeler `run_crawl(...)`.
def run_crawl(job_id: str, start_urls: list[str], project_id: int,
              limits: dict | None = None,
              progress_cb: Callable[[dict], None] | None = None,
              ctx=None, already_visited: set[str] | None = None) -> dict:
    """Fonction d'entrée du crawl (mêmes paramètres que `CrawlEngine.run`)."""
    return CrawlEngine().run(job_id, start_urls, project_id, limits, progress_cb, ctx, already_visited)
