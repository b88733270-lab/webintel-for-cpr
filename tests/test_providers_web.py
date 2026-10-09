"""Tests — providers Web généralistes (v3.0.0).

ZÉRO réseau réel : tous les appels HTTP passent par des
``httpx.MockTransport`` injectés. Aucun appel live n'est exercé ici.
"""

from __future__ import annotations

import json

import httpx
import pytest

from webintel.config.settings import get_settings
from webintel.providers.base import BaseProvider, ProviderErreur, ResultatProvider
from webintel.providers.duckduckgo import DuckDuckGoProvider
from webintel.providers.github import GitHubProvider
from webintel.providers.registre import (
    normaliser_url,
    rechercher_avec_fallback,
    statuts_providers,
)
from webintel.providers.rss_actualites import RssActualitesProvider
from webintel.providers.wikipedia import WikipediaProvider
from webintel.search.web_service import rechercher_web


def _transport(gestionnaire) -> httpx.MockTransport:
    return httpx.MockTransport(gestionnaire)


def _reponse_json(donnees, statut: int = 200) -> httpx.Response:
    return httpx.Response(statut, json=donnees)


WIKI_REPONSE = {
    "query": {
        "search": [
            {
                "title": "Moringa oleifera",
                "snippet": "Le <span>moringa</span> est un arbre tropical.",
                "timestamp": "2024-01-02T03:04:05Z",
            },
            {
                "title": "Moringa",
                "snippet": "Genre de plantes.",
                "timestamp": "2023-05-06T00:00:00Z",
            },
        ]
    }
}

DDG_REPONSE = {
    "Heading": "Moringa oleifera",
    "AbstractText": "Arbre tropical aux feuilles comestibles.",
    "AbstractURL": "https://fr.wikipedia.org/wiki/Moringa_oleifera",
    "RelatedTopics": [
        {"FirstURL": "https://example.org/moringa", "Text": "Moringa - Bienfaits et usages."},
        {"Name": "Groupe", "Topics": [{"FirstURL": "https://example.net/feuilles", "Text": "Feuilles de moringa - Nutrition."}]},
    ],
}

GITHUB_REPONSE = {
    "items": [
        {
            "full_name": "alice/moringa-data",
            "html_url": "https://github.com/alice/moringa-data",
            "description": "Données ouvertes sur le moringa",
            "language": "Python",
            "stargazers_count": 42,
            "updated_at": "2024-03-01T00:00:00Z",
        }
    ]
}

RSS_REPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Actualités</title>
<item><title>Sécheresse au Sahel</title><link>https://example.org/sahel</link>
<description>Les récoltes sont menacées.</description><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
<item><title>Marchés de Ouagadougou</title><link>https://example.net/marches</link>
<description>Prix des céréales.</description><pubDate>Tue, 02 Jan 2024 00:00:00 GMT</pubDate></item>
</channel></rss>"""


# ---------------------------------------------------------------------------
# Interface commune et parsing (sans clé)
# ---------------------------------------------------------------------------

def test_wikipedia_parse_resultats():
    def gestionnaire(request: httpx.Request) -> httpx.Response:
        assert request.url.host in ("fr.wikipedia.org", "en.wikipedia.org")
        return _reponse_json(WIKI_REPONSE)

    resultats = WikipediaProvider(transport=_transport(gestionnaire)).rechercher("moringa", limite=5)
    assert len(resultats) == 2
    assert resultats[0].titre == "Moringa oleifera"
    assert resultats[0].source == "wikipedia"
    assert resultats[0].type == "connaissances"
    assert "<span>" not in resultats[0].extrait
    assert resultats[0].url.startswith("https://fr.wikipedia.org/wiki/")


def test_wikipedia_decode_entites_html():
    # Régression v3.0.0 : les extraits Wikipedia contiennent des entités
    # HTML (&#039; pour l'apostrophe) qui s'affichaient telles quelles
    # dans l'écran Recherche Web. Elles doivent être décodées.
    reponse_entites = {
        "query": {
            "search": [
                {
                    "title": "Burkina Faso",
                    "snippet": "L&#039;économie du Burkina &amp; de la région &lt;Sahel&gt;",
                    "timestamp": "2024-01-02T03:04:05Z",
                },
            ]
        }
    }

    def gestionnaire(request: httpx.Request) -> httpx.Response:
        return _reponse_json(reponse_entites)

    resultats = WikipediaProvider(transport=_transport(gestionnaire)).rechercher("burkina", limite=5)
    assert len(resultats) == 1
    assert "&#039;" not in resultats[0].extrait
    assert "&amp;" not in resultats[0].extrait
    assert resultats[0].extrait == "L'économie du Burkina & de la région <Sahel>"


def test_duckduckgo_parse_resume_et_sujets():
    resultats = DuckDuckGoProvider(transport=_transport(lambda r: _reponse_json(DDG_REPONSE))).rechercher("moringa", limite=5)
    assert resultats[0].titre == "Moringa oleifera"
    assert resultats[0].type == "connaissances"
    urls = [r.url for r in resultats]
    assert "https://example.net/feuilles" in urls  # groupe imbriqué aplati


def test_github_parse_depots_sans_token():
    vu = {}

    def gestionnaire(request: httpx.Request) -> httpx.Response:
        vu["authorization"] = request.headers.get("authorization")
        return _reponse_json(GITHUB_REPONSE)

    resultats = GitHubProvider(transport=_transport(gestionnaire)).rechercher("moringa", limite=5)
    assert vu["authorization"] is None  # sans token : aucun en-tête inventé
    assert resultats[0].type == "code"
    assert "Python" in resultats[0].extrait


def test_rss_parse_flux():
    def gestionnaire(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=RSS_REPONSE, headers={"content-type": "application/rss+xml"})

    resultats = RssActualitesProvider(transport=_transport(gestionnaire)).rechercher("sahel", limite=5)
    assert [r.titre for r in resultats] == ["Sécheresse au Sahel", "Marchés de Ouagadougou"]
    assert resultats[0].type == "actualites"
    assert resultats[0].date is not None


def test_provider_erreur_http_normalisee():
    provider = WikipediaProvider(transport=_transport(lambda r: httpx.Response(503, text="indisponible")))
    with pytest.raises(ProviderErreur) as exc_info:
        provider.rechercher("moringa")
    assert exc_info.value.type_erreur == "http"
    assert "wikipedia" in str(exc_info.value)


def test_provider_timeout_normalise():
    def gestionnaire(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("trop lent", request=request)

    provider = DuckDuckGoProvider(transport=_transport(gestionnaire))
    with pytest.raises(ProviderErreur) as exc_info:
        provider.rechercher("moringa")
    assert exc_info.value.type_erreur == "timeout"


# ---------------------------------------------------------------------------
# Déduplication et fallback
# ---------------------------------------------------------------------------

def test_normaliser_url_dedup():
    a = normaliser_url("HTTPS://Example.ORG/page/?utm_source=x&b=2&a=1#frag")
    b = normaliser_url("https://example.org/page?a=1&b=2")
    assert a == b


class _ProviderFaux(BaseProvider):
    """Provider déterministe pour tester le registre sans réseau."""

    def __init__(self, nom: str, resultats: list[ResultatProvider] | None = None, erreur: ProviderErreur | None = None):
        super().__init__()
        self.nom = nom
        self.capacites = ["recherche_web"]
        self.necessite_cle = False
        self._resultats = resultats or []
        self._erreur = erreur

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        if self._erreur is not None:
            raise self._erreur
        return list(self._resultats)


def test_dedup_par_url_normalisee():
    r1 = ResultatProvider(titre="Un", url="https://example.org/a?utm_source=x", source="p1")
    r2 = ResultatProvider(titre="Un bis", url="https://example.org/a", source="p2")
    sortie = rechercher_avec_fallback(
        "requete", providers=[_ProviderFaux("p1", [r1]), _ProviderFaux("p2", [r2])], limite=10
    )
    assert len(sortie["resultats"]) == 1
    assert sortie["providers_utilises"] == ["p1", "p2"]


def test_fallback_a_en_echec_puis_b_repond():
    erreur = ProviderErreur("Provider « a » : délai dépassé.", type_erreur="timeout")
    ok = ResultatProvider(titre="Réponse B", url="https://example.org/b", source="b")
    sortie = rechercher_avec_fallback(
        "requete", providers=[_ProviderFaux("a", erreur=erreur), _ProviderFaux("b", [ok])], limite=10
    )
    assert [r["titre"] for r in sortie["resultats"]] == ["Réponse B"]
    assert sortie["providers_utilises"] == ["b"]
    assert sortie["providers_en_echec"][0]["provider"] == "a"
    assert sortie["providers_en_echec"][0]["type_erreur"] == "timeout"


def test_registre_borne_limite():
    resultats = [ResultatProvider(titre=f"R{i}", url=f"https://example.org/{i}", source="p") for i in range(8)]
    sortie = rechercher_avec_fallback("requete", providers=[_ProviderFaux("p", resultats)], limite=3)
    assert len(sortie["resultats"]) == 3


# ---------------------------------------------------------------------------
# Providers à clé : saut propre, aucun secret exposé
# ---------------------------------------------------------------------------

def test_provider_a_cle_absent_saute_proprement(monkeypatch):
    # Aucune clé dans la configuration (état par défaut) : saut sans exception globale.
    sortie = rechercher_avec_fallback("moringa", limite=5)
    noms_echec = {e["provider"] for e in sortie["providers_en_echec"]}
    for provider_a_cle in ("brave", "tavily", "serpapi", "newsapi"):
        assert provider_a_cle not in noms_echec  # sautés, pas en échec bruyant


def test_statuts_sans_fuite_de_cle(monkeypatch):
    parametres = get_settings()
    monkeypatch.setattr(parametres, "WEBINTEL_BRAVE_API_KEY", "cle-super-secrete-ne-doit-pas-fuiter")
    statuts = statuts_providers()
    brut = json.dumps(statuts, ensure_ascii=False)
    assert "cle-super-secrete" not in brut
    brave = next(s for s in statuts if s["nom"] == "brave")
    assert brave["statut"] == "pret"
    assert brave["configure"] is True


def test_statuts_defaut_sans_cle():
    statuts = {s["nom"]: s for s in statuts_providers()}
    for nom in ("wikipedia", "duckduckgo", "github", "rss_actualites"):
        assert statuts[nom]["statut"] == "pret", nom
        assert statuts[nom]["configure"] is True, nom
    for nom in ("brave", "tavily", "serpapi", "newsapi"):
        assert statuts[nom]["statut"] == "non_configure", nom
        assert statuts[nom]["configure"] is False, nom
        assert statuts[nom]["necessite_cle"] is True, nom


def test_service_recherche_web_structure(monkeypatch):
    # Le service retourne la structure complète même quand le réseau
    # des providers sans clé répond (mocké) ou échoue honnêtement.
    def gestionnaire(request: httpx.Request) -> httpx.Response:
        if "wikipedia" in request.url.host:
            return _reponse_json(WIKI_REPONSE)
        return _reponse_json({})

    from webintel.providers import registre as registre_module

    monkeypatch.setattr(
        registre_module, "construire_registre",
        lambda **kwargs: [WikipediaProvider(transport=_transport(gestionnaire))],
    )
    sortie = rechercher_web("  moringa   oleifera ", limite=5, langue="fr")
    assert sortie["requete"] == "moringa oleifera"
    assert sortie["langue"] == "fr"
    assert sortie["resultats"][0]["source"] == "wikipedia"
    assert "duree_ms" in sortie


# ---------------------------------------------------------------------------
# Endpoints API
# ---------------------------------------------------------------------------

def test_api_providers_sans_cle(client):
    reponse = client.get("/api/web/providers")
    assert reponse.status_code == 200, reponse.text[:300]
    donnees = reponse.json()
    assert donnees["count"] >= 8
    brut = reponse.text
    parametres = get_settings()
    for champ in ("WEBINTEL_BRAVE_API_KEY", "WEBINTEL_TAVILY_API_KEY",
                  "WEBINTEL_SERPAPI_API_KEY", "WEBINTEL_NEWSAPI_API_KEY"):
        valeur = getattr(parametres, champ, "")
        if valeur:
            assert valeur not in brut


def test_api_recherche_validation(client):
    assert client.get("/api/web/recherche").status_code == 422
    assert client.get("/api/web/recherche", params={"q": "x", "langue": "de"}).status_code == 422
    assert client.get("/api/web/recherche", params={"q": "x", "limite": 99}).status_code == 422


def test_api_recherche_avec_mocks(client, monkeypatch):
    def gestionnaire(request: httpx.Request) -> httpx.Response:
        if "wikipedia" in request.url.host:
            return _reponse_json(WIKI_REPONSE)
        if "duckduckgo" in request.url.host:
            return _reponse_json(DDG_REPONSE)
        if "api.github.com" in request.url.host:
            return _reponse_json({"items": []})
        if "news.google.com" in request.url.host:
            return httpx.Response(200, text=RSS_REPONSE)
        return _reponse_json({})

    from webintel.providers import registre as registre_module

    transport = _transport(gestionnaire)
    monkeypatch.setattr(
        registre_module, "construire_registre",
        lambda **kwargs: _registre_mock(transport),
    )
    reponse = client.get("/api/web/recherche", params={"q": "moringa", "limite": 5})
    assert reponse.status_code == 200, reponse.text[:500]
    donnees = reponse.json()
    assert donnees["requete"] == "moringa"
    assert len(donnees["resultats"]) >= 1
    assert donnees["resultats"][0]["source"] in ("wikipedia", "duckduckgo", "rss_actualites")


def _registre_mock(transport):
    from webintel.providers.avec_cle import BraveProvider, NewsApiProvider, SerpApiProvider, TavilyProvider

    return [
        BraveProvider(transport=transport),
        TavilyProvider(transport=transport),
        SerpApiProvider(transport=transport),
        WikipediaProvider(transport=transport),
        DuckDuckGoProvider(transport=transport),
        RssActualitesProvider(transport=transport),
        NewsApiProvider(transport=transport),
        GitHubProvider(transport=transport),
    ]
