"""Découverte de liens dans une page HTML (factorisation de l'ancien scraper).

`extract_links(html, base_url)` retourne une liste de dicts `{url, texte}` :
- URLs relatives résolues contre `base_url` (`urljoin`),
- schémas `javascript:`, `mailto:`, `tel:` (et fragments purs `#...`) exclus,
- déduplication stricte sur `normalization.urls.canonical_url`.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from webintel.normalization.urls import canonical_url

# Schémas d'ancres à ignorer (pas des pages à crawler).
_SKIPPED_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "ftp:")


def _is_skippable(href: str) -> bool:
    stripped = href.strip().lower()
    if not stripped or stripped.startswith("#"):
        return True
    return stripped.startswith(_SKIPPED_SCHEMES)


def extract_links(html: str, base_url: str) -> list[dict]:
    """Extrait les liens `{url, texte}` d'un HTML, résolus et dédupliqués.

    `url` est l'URL absolue résolue (non canonique, pour préserver l'originale).
    La déduplication se fait sur `canonical_url` (tracking supprimé).
    """
    if not html or not base_url:
        return []
    soup = BeautifulSoup(html, "lxml")
    links: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if _is_skippable(href):
            continue
        resolved = urljoin(base_url, href)
        scheme = urlparse(resolved).scheme.lower()
        if scheme not in ("http", "https"):
            continue
        try:
            canon = canonical_url(resolved)
        except Exception:
            continue
        if canon in seen:
            continue
        seen.add(canon)
        links.append({"url": resolved, "texte": anchor.get_text(strip=True)})
    return links
