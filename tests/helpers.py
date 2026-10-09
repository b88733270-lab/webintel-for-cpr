"""Utilitaires partagés des tests (aucun réseau externe)."""

from __future__ import annotations

import time

from bs4 import BeautifulSoup

from webintel.core.types import FetchResult
from webintel.core.utils import clean_text, utcnow


def html_to_text(html: str) -> str:
    """Texte visible d'un HTML (scripts/styles retirés)."""
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return clean_text(soup.get_text(separator=" "))


def make_fetch_result(url: str, html: str, status_code: int = 200) -> FetchResult:
    """Construit un `FetchResult` simulé depuis un HTML de fixture."""
    return FetchResult(
        url=url,
        final_url=url,
        status_code=status_code,
        headers={"content-type": "text/html; charset=utf-8"},
        html=html,
        text=html_to_text(html),
        fetched_at=utcnow(),
        duration_ms=12,
        method="http",
    )


def make_fetcher(pages: dict[str, str]):
    """Fabrique un `fetch(self, url, *, timeout_s=None)` mocké.

    `pages` : `{url: html}` — la recherche est tolérante aux barres
    obliques finales et aux URLs canoniques.
    """

    def _fetch(self, url: str, *, timeout_s: int | None = None) -> FetchResult:
        html = (
            pages.get(url)
            or pages.get(url.rstrip("/"))
            or pages.get(url.rstrip("/") + "/")
        )
        if html is None:
            return FetchResult(
                url=url, final_url=url, status_code=404, fetched_at=utcnow(),
                error="Page introuvable (fixture mockée).",
            )
        return make_fetch_result(url, html)

    return _fetch


def wait_for(predicate, timeout_s: float = 30.0, interval_s: float = 0.2):
    """Attente active avec deadline (stabilise les tests async/threads)."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval_s)
    return last
