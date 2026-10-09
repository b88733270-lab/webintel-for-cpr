"""Respect de robots.txt : récupération + cache par domaine, `can_fetch()`.

En cas d'échec de récupération du robots.txt : fail-open (autorisé) avec log,
conformément au contrat §5 d'architecture.md.
"""

from __future__ import annotations

import threading
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from webintel.config.settings import get_settings
from webintel.core.logging import get_logger
from webintel.security.pinned_transport import client_http_sure

logger = get_logger(__name__)


class RobotChecker:
    """Vérifie les autorisations robots.txt avec cache mémoire par domaine."""

    def __init__(self, timeout_s: float = 5.0) -> None:
        self._timeout = timeout_s
        self._cache: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def _download_parser(self, domain: str) -> RobotFileParser | None:
        """Télécharge et parse /robots.txt ; None si absent ou illisible."""
        for scheme in ("https", "http"):
            try:
                # Transport épinglé anti-TOCTOU (chaque redirection
                # re-épingle et re-valide son hôte).
                with client_http_sure(
                    timeout=self._timeout,
                    headers={"User-Agent": get_settings().DEFAULT_UA},
                ) as client:
                    resp = client.get(
                        f"{scheme}://{domain}/robots.txt",
                        follow_redirects=True,
                    )
                if resp.status_code == 404:
                    return None  # pas de robots.txt -> tout autorisé
                if resp.status_code < 400 and resp.text.strip():
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
                    return parser
                return None
            except Exception as exc:  # réseau, timeout, TLS... : fail-open
                logger.warning(
                    "robots.txt illisible pour %s (%s) — accès autorisé (fail-open)",
                    domain,
                    exc,
                )
                return None
        return None

    def _get_parser(self, domain: str) -> RobotFileParser | None:
        with self._lock:
            if domain in self._cache:
                return self._cache[domain]
        parser = self._download_parser(domain)
        with self._lock:
            self._cache[domain] = parser
        return parser

    def can_fetch(self, url: str, user_agent: str) -> bool:
        """True si le crawl de `url` est autorisé par robots.txt (fail-open)."""
        domain = (urlparse(url).hostname or "").lower()
        if not domain:
            return False
        parser = self._get_parser(domain)
        if parser is None:
            return True
        try:
            return bool(parser.can_fetch(user_agent, url))
        except Exception:
            return True  # règle illisible -> fail-open

    def clear_cache(self) -> None:
        """Vide le cache (utile en tests)."""
        with self._lock:
            self._cache.clear()
