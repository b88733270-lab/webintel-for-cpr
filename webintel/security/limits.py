"""Limites de récupération/crawl (surchargables via `Settings` / environnement).

Valeurs par défaut : contrat §5 d'architecture.md.
"""

from __future__ import annotations

from webintel.config.settings import get_settings
from webintel.core.exceptions import FetchError

_settings = get_settings()

MAX_RESPONSE_BYTES: int = _settings.MAX_RESPONSE_BYTES
FETCH_TIMEOUT_S: int = _settings.FETCH_TIMEOUT_S
MAX_REDIRECTS: int = _settings.MAX_REDIRECTS
MAX_CRAWL_PAGES: int = _settings.MAX_CRAWL_PAGES
MAX_CRAWL_DEPTH: int = _settings.MAX_CRAWL_DEPTH
MAX_CONCURRENCY: int = _settings.MAX_CONCURRENCY
CRAWL_DELAY_S: float = _settings.CRAWL_DELAY_S


def check_response_size(n_bytes: int) -> None:
    """Lève `FetchError` si la réponse dépasse `MAX_RESPONSE_BYTES`."""
    if n_bytes > MAX_RESPONSE_BYTES:
        raise FetchError(
            f"Réponse trop volumineuse : {n_bytes} octets "
            f"(limite : {MAX_RESPONSE_BYTES} octets)."
        )
