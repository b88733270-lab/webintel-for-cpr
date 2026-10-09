"""Récupération HTTP/HTML des pages (collecte).

- `BaseFetcher` : interface abstraite de collecte.
- `HttpFetcher` : client httpx avec redirections gérées **manuellement** (revalidation
  anti-SSRF de chaque `Location`, contrat §5), limite de taille en streaming.
- `BrowserFetcher` : STUB documenté — le rendu navigateur (Playwright) arrive en v2.
- `HybridFetcher` : HTTP d'abord ; signale `needs_js=True` quand la page semble
  nécessiter JavaScript (le rendu réel sera branché en v2).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from webintel.config.settings import get_settings
from webintel.core.exceptions import FetchError, SecurityError
from webintel.core.logging import get_logger
from webintel.core.types import FetchResult
from webintel.core.utils import clean_text, utcnow
from webintel.security.pinned_transport import PinnedHTTPTransport
from webintel.security.ssrf import validate_url

logger = get_logger(__name__)

# Content-Type considérés comme du HTML à parser.
_HTML_MARKERS = ("text/html", "application/xhtml+xml")


def _html_to_text(html: str) -> str:
    """Extrait le texte visible d'un HTML (scripts/styles retirés, espaces nettoyés)."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return clean_text(soup.get_text(separator=" "))


def _is_html(content_type: str) -> bool:
    lowered = (content_type or "").lower()
    return any(marker in lowered for marker in _HTML_MARKERS)


class BaseFetcher(ABC):
    """Interface de collecte : `fetch(url) -> FetchResult`."""

    @abstractmethod
    def fetch(self, url: str, *, timeout_s: int | None = None) -> FetchResult:
        """Récupère `url` et retourne un `FetchResult` (ne lève que `SecurityError`
        ou `FetchError` bloquant : URL invalide, réponse trop volumineuse, stub v2)."""
        raise NotImplementedError


class HttpFetcher(BaseFetcher):
    """Fetcher HTTP via httpx (sans suivi auto des redirections).

    Chaque redirection est suivie manuellement puis revalidée par
    `security.ssrf.validate_url` (anti DNS-rebinding, contrat §5). La
    connexion utilise `PinnedHTTPTransport` : résolution DNS unique et
    épinglée (anti-TOCTOU), Host et SNI préservés. La taille
    de réponse est bornée en lecture streaming (`FetchError` au-delà du seuil).
    Les erreurs réseau/timeout/statut HTTP >= 400 sont encapsulées dans
    `FetchResult.error` (message FR) — jamais levées.
    """

    def __init__(self, timeout_s: int | None = None) -> None:
        self._settings = get_settings()
        self._timeout = timeout_s if timeout_s is not None else self._settings.FETCH_TIMEOUT_S
        client_kwargs = dict(
            timeout=self._timeout,
            verify=True,
            follow_redirects=False,  # redirections manuelles (revalidation SSRF)
            # Anti-TOCTOU : la connexion utilise l'IP validée par
            # `validate_url` (résolution unique, Host/SNI préservés) au
            # lieu de refaire sa propre résolution DNS.
            transport=PinnedHTTPTransport(),
            headers={
                "User-Agent": self._settings.DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
        )
        try:
            self._client = httpx.Client(**client_kwargs)
        except Exception as exc:
            # Variables d'environnement proxy malformées (ex. no_proxy exotique)
            # : repli sans prise en compte de l'environnement plutôt que planter.
            logger.warning("httpx.Client avec l'environnement a échoué (%s) — repli sans proxy env.", exc)
            self._client = httpx.Client(trust_env=False, **client_kwargs)

    def _stream_body(self, response: httpx.Response) -> str:
        """Lit le corps en morceaux, en bornant la taille (lève `FetchError`)."""
        chunks: list[bytes] = []
        total = 0
        max_bytes = self._settings.MAX_RESPONSE_BYTES
        for chunk in response.iter_bytes(chunk_size=65536):
            total += len(chunk)
            if total > max_bytes:
                raise FetchError(
                    f"Réponse trop volumineuse : plus de {max_bytes} octets "
                    f"(limite : {max_bytes} octets)."
                )
            chunks.append(chunk)
        raw = b"".join(chunks)
        # httpx détecte l'encodage via les en-têtes / le contenu ; repli UTF-8.
        return raw.decode(response.encoding or "utf-8", errors="replace")

    def fetch(self, url: str, *, timeout_s: int | None = None) -> FetchResult:
        started = time.monotonic()
        try:
            current = validate_url(url)
        except SecurityError:
            raise  # les refus de sécurité remontent toujours (pas d'encapsulation)

        max_redirects = self._settings.MAX_REDIRECTS
        timeout = timeout_s if timeout_s is not None else self._timeout
        history: list[str] = []
        try:
            for _ in range(max_redirects + 1):
                try:
                    req_cm = self._client.stream("GET", current, timeout=timeout)
                except httpx.TimeoutException:
                    return self._error_result(url, current, started, "Délai de connexion dépassé (timeout).")
                except httpx.ConnectError:
                    return self._error_result(url, current, started, "Connexion impossible au serveur (hôte injoignable ou refusé).")
                except httpx.HTTPError as exc:
                    return self._error_result(url, current, started, f"Erreur réseau : {exc}.")

                with req_cm as response:
                    status = response.status_code
                    headers = {str(k): str(v) for k, v in response.headers.multi_items()}
                    if status in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            return self._error_result(url, current, started, f"Redirection HTTP {status} sans en-tête Location.", status)
                        history.append(current)
                        # Revalidation anti-SSRF de chaque cible de redirection.
                        try:
                            current = validate_url(urljoin(current, location))
                        except SecurityError as exc:
                            return self._error_result(url, current, started, f"Redirection bloquée (sécurité) : {exc}.", status)
                        continue

                    final_url = current
                    duration_ms = int((time.monotonic() - started) * 1000)
                    if status >= 400:
                        return FetchResult(
                            url=url, final_url=final_url, status_code=status,
                            headers=headers, fetched_at=utcnow(), duration_ms=duration_ms,
                            method="http",
                            error=f"Le serveur a répondu avec le statut HTTP {status}.",
                        )
                    try:
                        html = self._stream_body(response) if _is_html(headers.get("content-type", "")) else ""
                    except FetchError:
                        raise  # limite de taille -> remonte (bloquant, contrat)
                    text = _html_to_text(html) if html else ""
                    return FetchResult(
                        url=url, final_url=final_url, status_code=status,
                        headers=headers, html=html, text=text,
                        fetched_at=utcnow(), duration_ms=duration_ms, method="http",
                    )
            return self._error_result(url, current, started, f"Trop de redirections (limite : {max_redirects}).")
        except FetchError:
            raise
        except SecurityError:
            raise
        except Exception as exc:  # garde-fou : jamais d'exception brute
            logger.exception("Échec inattendu du fetch de %s", url)
            return self._error_result(url, current, started, f"Erreur inattendue lors de la récupération : {exc}.")

    def _error_result(self, url: str, final_url: str, started: float, message: str, status: int = 0) -> FetchResult:
        return FetchResult(
            url=url, final_url=final_url, status_code=status,
            fetched_at=utcnow(), duration_ms=int((time.monotonic() - started) * 1000),
            method="http", error=message,
        )

    def close(self) -> None:
        """Ferme le client httpx sous-jacent."""
        self._client.close()


class BrowserFetcher(BaseFetcher):
    """STUB v1 — rendu navigateur prévu en v2 (Playwright interdit en v1, contrat §1).

    Même signature que `HttpFetcher` pour une substitution transparente future.
    """

    def fetch(self, url: str, *, timeout_s: int | None = None) -> FetchResult:
        raise FetchError(
            "Rendu navigateur (Playwright) prévu en v2 — la collecte HTTP reste disponible."
        )


class HybridFetcher(BaseFetcher):
    """Collecte HTTP d'abord, détection du besoin JavaScript ensuite.

    `fetch()` retourne toujours le `FetchResult` HTTP ; si `_needs_js()` détecte
    une page quasi vide typique d'une SPA, le résultat porte `needs_js=True`
    (le rendu navigateur sera branché en v2). `fetch_with_fallback()` est un
    alias documenté : en v2 il tentera le navigateur quand `needs_js` est vrai.
    """

    # Marqueurs typiques d'applications monopages / pages exigeant du JS.
    _SPA_MARKERS = (
        '<div id="root"',
        '<div id="app"',
        "__NEXT_DATA__",
        "__NUXT__",
        "window.__INITIAL_STATE__",
        "enable javascript",
        "activez javascript",
        "javascript est désactivé",
        "javascript is disabled",
        "please enable javascript",
        "veuillez activer javascript",
        "data-reactroot",
        "ng-app",
    )

    def __init__(self, http_fetcher: HttpFetcher | None = None) -> None:
        self._http = http_fetcher or HttpFetcher()

    def _needs_js(self, html: str, headers: dict) -> bool:
        """True si la page semble exiger un navigateur (body quasi vide + marqueurs SPA).

        `headers` est accepté pour une compatibilité future (ex. détection via
        en-têtes) ; l'analyse porte aujourd'hui sur le HTML.
        """
        if not html:
            return False
        text = _html_to_text(html)
        if len(text) >= 500:
            return False  # contenu substantiel -> pas besoin de JS
        lowered = html.lower()
        return any(marker in lowered for marker in self._SPA_MARKERS)

    def fetch(self, url: str, *, timeout_s: int | None = None) -> FetchResult:
        result = self._http.fetch(url, timeout_s=timeout_s)
        if result.error is None and result.status_code == 200 and result.html:
            result.needs_js = self._needs_js(result.html, result.headers)
        return result

    def fetch_with_fallback(self, url: str, *, timeout_s: int | None = None) -> FetchResult:
        """Alias de `fetch()`.

        En v2, quand `needs_js` sera vrai, cette méthode tentera automatiquement
        le rendu navigateur (`BrowserFetcher`) avant de retourner le résultat.
        """
        return self.fetch(url, timeout_s=timeout_s)

    def close(self) -> None:
        self._http.close()
