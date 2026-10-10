"""Registre ordonné des providers + recherche avec fallback automatique.

``rechercher_avec_fallback`` essaie les providers dans l'ordre du
registre, accumule les résultats, déduplique par URL normalisée, et
journalise quel provider a répondu et lesquels ont échoué — sans
JAMAIS logger de clé API. Un provider non configuré est sauté
proprement ; un provider en échec est déclaré en échec (jamais de
faux résultat inventé). Le budget temps global borne la recherche
complète : quand il est épuisé, les providers restants ne sont pas
tentés et l'arrêt est journalisé.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from webintel.config.settings import get_settings
from webintel.core.logging import get_logger
from webintel.providers.avec_cle import (
    BraveProvider,
    NewsApiProvider,
    SerpApiProvider,
    TavilyProvider,
)
from webintel.providers.base import BaseProvider, ProviderErreur, ResultatProvider
from webintel.providers.duckduckgo import DuckDuckGoProvider
from webintel.providers.github import GitHubProvider
from webintel.providers.rss_actualites import RssActualitesProvider
from webintel.providers.scientifique import (
    ChEMBLProvider,
    CrossrefProvider,
    EuropePmcProvider,
    GbifProvider,
    OpenAlexProvider,
    PubChemProvider,
    PubMedProvider,
    SemanticScholarProvider,
)
from webintel.providers.wikipedia import WikipediaProvider

logger = get_logger(__name__)

_PARAMETRES_TRACKING = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
)


def normaliser_url(url: str) -> str:
    """Normalise une URL pour la déduplication.

    Minuscules sur le schéma/hôte, barre oblique finale retirée,
    fragment supprimé, paramètres de pistage (utm_*, fbclid, gclid)
    retirés, paramètres restants triés. Deux URL qui ne diffèrent que
    par ces détails sont considérées identiques.
    """
    try:
        morceaux = urlparse((url or "").strip())
    except ValueError:
        return (url or "").strip().lower()
    scheme = (morceaux.scheme or "https").lower()
    netloc = (morceaux.netloc or "").lower()
    path = morceaux.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    params = [
        (cle, valeur)
        for cle, valeur in parse_qsl(morceaux.query, keep_blank_values=True)
        if cle.lower() not in _PARAMETRES_TRACKING
    ]
    params.sort()
    return urlunparse((scheme, netloc, path, "", urlencode(params), ""))


def construire_registre(
    *, transport: httpx.BaseTransport | None = None
) -> list[BaseProvider]:
    """Registre ordonné : à clé d'abord (souvent meilleurs, si configurés),
    puis les providers généralistes sans clé, puis les providers
    scientifiques sans clé (v3.1.0).

    L'ordre de repli : Brave → Tavily → SerpApi → Wikipedia →
    DuckDuckGo → RSS actualités → NewsAPI → GitHub → PubMed →
    OpenAlex → Europe PMC → Crossref → Semantic Scholar → PubChem →
    ChEMBL → GBIF. Les providers non configurés sont sautés à
    l'exécution (pas d'exception globale).
    """
    return [
        BraveProvider(transport=transport),
        TavilyProvider(transport=transport),
        SerpApiProvider(transport=transport),
        WikipediaProvider(transport=transport),
        DuckDuckGoProvider(transport=transport),
        RssActualitesProvider(transport=transport),
        NewsApiProvider(transport=transport),
        GitHubProvider(transport=transport),
        PubMedProvider(transport=transport),
        OpenAlexProvider(transport=transport),
        EuropePmcProvider(transport=transport),
        CrossrefProvider(transport=transport),
        SemanticScholarProvider(transport=transport),
        PubChemProvider(transport=transport),
        ChEMBLProvider(transport=transport),
        GbifProvider(transport=transport),
    ]


def statuts_providers(
    *, transport: httpx.BaseTransport | None = None
) -> list[dict[str, Any]]:
    """Statuts de tous les providers (page Paramètres) — jamais de clé."""
    return [p.statut() for p in construire_registre(transport=transport)]


def rechercher_avec_fallback(
    requete: str,
    *,
    limite: int = 10,
    langue: str = "fr",
    timeout_s: float | None = None,
    budget_s: float | None = None,
    providers: list[BaseProvider] | None = None,
    capacite: str | None = None,
) -> dict[str, Any]:
    """Recherche avec fallback automatique entre providers.

    Retourne ``{requete, resultats, providers_utilises, providers_en_echec,
    duree_ms}`` où ``resultats`` est une liste de dicts (voir
    ``ResultatProvider.vers_dict``), dédupliquée par URL normalisée et
    bornée à ``limite``. Les échecs sont déclarés honnêtement : aucun
    résultat inventé ne remplace un provider en panne.

    - ``providers`` : registre imposé (tests) ; sinon ``construire_registre()`` ;
    - ``capacite`` : ne retient que les providers déclarant cette capacité ;
    - ``timeout_s`` / ``budget_s`` : défauts depuis la configuration
      (``WEBINTEL_PROVIDER_TIMEOUT_S`` / ``WEBINTEL_PROVIDER_BUDGET_S``).
    """
    requete = (requete or "").strip()
    if not requete:
        return {
            "requete": requete,
            "resultats": [],
            "providers_utilises": [],
            "providers_en_echec": [
                {"provider": "registre", "type_erreur": "http",
                 "message": "Requête vide : aucun provider interrogé."}
            ],
            "duree_ms": 0,
        }
    parametres = get_settings()
    timeout_par_provider = float(timeout_s if timeout_s is not None else parametres.WEBINTEL_PROVIDER_TIMEOUT_S)
    budget_total = float(budget_s if budget_s is not None else parametres.WEBINTEL_PROVIDER_BUDGET_S)
    registre = providers if providers is not None else construire_registre()
    if capacite:
        registre = [p for p in registre if capacite in p.capacites]

    debut = time.monotonic()
    resultats: list[ResultatProvider] = []
    urls_vues: set[str] = set()
    utilises: list[str] = []
    echecs: list[dict[str, str]] = []

    for provider in registre:
        if len(resultats) >= limite:
            break
        ecoule = time.monotonic() - debut
        restant = budget_total - ecoule
        if restant <= 0:
            logger.info(
                "Recherche Web : budget global de %.0f s épuisé avant « %s » — arrêt propre.",
                budget_total, provider.nom,
            )
            break
        statut = provider.statut()
        if statut.get("statut") == "non_configure":
            logger.debug("Provider « %s » sauté : non configuré.", provider.nom)
            continue
        timeout_effectif = max(0.5, min(timeout_par_provider, restant))
        try:
            trouves = provider.rechercher(
                requete, limite=limite, langue=langue, timeout_s=timeout_effectif
            )
        except ProviderErreur as exc:
            logger.warning(
                "Provider « %s » en échec (%s) : %s", provider.nom, exc.type_erreur, exc
            )
            echecs.append(
                {"provider": provider.nom, "type_erreur": exc.type_erreur, "message": str(exc)}
            )
            continue
        except Exception as exc:  # un provider ne casse jamais la recherche
            logger.warning("Provider « %s » en échec inattendu : %s", provider.nom, exc)
            echecs.append(
                {"provider": provider.nom, "type_erreur": "http",
                 "message": f"Erreur inattendue du provider ({type(exc).__name__}). Aucun résultat récupéré."}
            )
            continue
        if provider.nom not in utilises:
            utilises.append(provider.nom)
        for resultat in trouves:
            cle_url = normaliser_url(resultat.url)
            if not cle_url or cle_url in urls_vues:
                continue
            urls_vues.add(cle_url)
            resultats.append(resultat)
            if len(resultats) >= limite:
                break
        logger.debug(
            "Provider « %s » : %d résultat(s) nouveau(x) après dédup (total %d).",
            provider.nom, len(trouves), len(resultats),
        )

    duree_ms = int((time.monotonic() - debut) * 1000)
    logger.info(
        "Recherche Web %r : %d résultat(s), providers utilisés=%s, échecs=%d, %d ms.",
        requete, len(resultats), utilises, len(echecs), duree_ms,
    )
    return {
        "requete": requete,
        "resultats": [r.vers_dict() for r in resultats],
        "providers_utilises": utilises,
        "providers_en_echec": echecs,
        "duree_ms": duree_ms,
    }
