"""Providers à clé optionnels : Brave, Tavily, SerpApi, NewsAPI.

Sans clé : ``statut()`` vaut ``non_configure`` et ``rechercher()`` lève
``ProviderErreur(non_configure)`` — le registre les saute proprement,
jamais d'exception qui casse la recherche globale. Les clés ne sont
jamais journalisées ni exposées dans les statuts.
"""

from __future__ import annotations

from webintel.config.settings import get_settings
from webintel.providers.base import (
    BaseProvider,
    ProviderErreur,
    ResultatProvider,
    nettoyer_extrait,
)


def _cle_depuis_settings(champ: str) -> str:
    try:
        return (getattr(get_settings(), champ, "") or "").strip()
    except Exception:
        return ""


class BraveProvider(BaseProvider):
    nom = "brave"
    capacites = ["recherche_web", "actualites"]
    necessite_cle = True
    hotes_autorises = frozenset({"api.search.brave.com"})

    def _cle(self) -> str:
        return _cle_depuis_settings("WEBINTEL_BRAVE_API_KEY")

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        cle = self._exiger_cle()
        donnees = self._get_json(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": requete, "count": max(1, limite),
                    "search_lang": "en" if (langue or "fr").startswith("en") else "fr"},
            headers={"X-Subscription-Token": cle, "Accept": "application/json"},
            timeout_s=timeout_s,
        )
        resultats: list[ResultatProvider] = []
        try:
            lignes = (donnees.get("web") or {}).get("results") or []
        except AttributeError as exc:
            raise ProviderErreur(
                "Provider « brave » : réponse inattendue. Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            ) from exc
        for ligne in lignes:
            url = (ligne.get("url") or "").strip()
            titre = (ligne.get("title") or "").strip()
            if not url or not titre:
                continue
            resultats.append(ResultatProvider(
                titre=titre, url=url,
                extrait=nettoyer_extrait(ligne.get("description")),
                source=self.nom, date=(ligne.get("age") or None), type="web",
            ))
        return resultats


class TavilyProvider(BaseProvider):
    nom = "tavily"
    capacites = ["recherche_web"]
    necessite_cle = True
    hotes_autorises = frozenset({"api.tavily.com"})

    def _cle(self) -> str:
        return _cle_depuis_settings("WEBINTEL_TAVILY_API_KEY")

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        cle = self._exiger_cle()
        donnees = self._post_json(
            "https://api.tavily.com/search",
            json_body={"api_key": cle, "query": requete, "max_results": max(1, limite)},
            timeout_s=timeout_s,
        )
        if not isinstance(donnees, dict):
            raise ProviderErreur(
                "Provider « tavily » : réponse inattendue. Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            )
        resultats: list[ResultatProvider] = []
        for ligne in donnees.get("results") or []:
            url = (ligne.get("url") or "").strip()
            titre = (ligne.get("title") or "").strip()
            if not url or not titre:
                continue
            resultats.append(ResultatProvider(
                titre=titre, url=url,
                extrait=nettoyer_extrait(ligne.get("content")),
                source=self.nom, date=(ligne.get("published_date") or None), type="web",
            ))
        return resultats


class SerpApiProvider(BaseProvider):
    nom = "serpapi"
    capacites = ["recherche_web"]
    necessite_cle = True
    hotes_autorises = frozenset({"serpapi.com"})

    def _cle(self) -> str:
        return _cle_depuis_settings("WEBINTEL_SERPAPI_API_KEY")

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        cle = self._exiger_cle()
        # NB : SerpApi impose la clé en paramètre d'URL. Elle n'est
        # jamais journalisée (les journaux ne contiennent que le nom du
        # provider et le type d'erreur).
        donnees = self._get_json(
            "https://serpapi.com/search.json",
            params={"q": requete, "api_key": cle, "num": max(1, limite),
                    "hl": "en" if (langue or "fr").startswith("en") else "fr",
                    "engine": "google"},
            timeout_s=timeout_s,
        )
        if not isinstance(donnees, dict):
            raise ProviderErreur(
                "Provider « serpapi » : réponse inattendue. Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            )
        resultats: list[ResultatProvider] = []
        for ligne in donnees.get("organic_results") or []:
            url = (ligne.get("link") or "").strip()
            titre = (ligne.get("title") or "").strip()
            if not url or not titre:
                continue
            resultats.append(ResultatProvider(
                titre=titre, url=url,
                extrait=nettoyer_extrait(ligne.get("snippet")),
                source=self.nom, date=(ligne.get("date") or None), type="web",
            ))
        return resultats


class NewsApiProvider(BaseProvider):
    nom = "newsapi"
    capacites = ["actualites"]
    necessite_cle = True
    hotes_autorises = frozenset({"newsapi.org"})

    def _cle(self) -> str:
        return _cle_depuis_settings("WEBINTEL_NEWSAPI_API_KEY")

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        cle = self._exiger_cle()
        donnees = self._get_json(
            "https://newsapi.org/v2/everything",
            params={"q": requete, "pageSize": max(1, limite), "apiKey": cle,
                    "language": "en" if (langue or "fr").startswith("en") else "fr",
                    "sortBy": "relevancy"},
            timeout_s=timeout_s,
        )
        if not isinstance(donnees, dict):
            raise ProviderErreur(
                "Provider « newsapi » : réponse inattendue. Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            )
        resultats: list[ResultatProvider] = []
        for article in donnees.get("articles") or []:
            url = (article.get("url") or "").strip()
            titre = (article.get("title") or "").strip()
            if not url or not titre:
                continue
            resultats.append(ResultatProvider(
                titre=titre, url=url,
                extrait=nettoyer_extrait(article.get("description")),
                source=self.nom, date=(article.get("publishedAt") or None), type="actualites",
            ))
        return resultats
