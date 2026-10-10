"""Abstraction Provider unifiée — recherche Web généraliste (v3.0.0).

Un *provider* est une source de recherche externe en LECTURE SEULE,
derrière une interface commune typée (``base.BaseProvider``) :

- providers SANS clé, fonctionnels par défaut : Wikipedia, DuckDuckGo
  Instant Answers, GitHub public, flux RSS d'actualités (Google News
  RSS) ;
- providers À CLÉ optionnels : Brave Search, Tavily, SerpApi, NewsAPI —
  sans clé, ils sont simplement sautés (statut ``non_configure``), jamais
  une exception qui casse la recherche ;
- providers SCIENTIFIQUES sans clé (v3.1.0) : PubMed (E-utilities),
  OpenAlex, Europe PMC, Crossref, Semantic Scholar (publications) ;
  PubChem, ChEMBL (chimie) ; GBIF (biodiversité). Capacité
  ``"recherche_scientifique"``, exposée par
  ``GET /api/web/recherche-scientifique``.

``registre.rechercher_avec_fallback`` essaie les providers dans l'ordre,
accumule et déduplique les résultats par URL normalisée, journalise
quel provider a répondu et lesquels ont échoué — sans JAMAIS logger
de clé API. Aucun résultat n'est inventé : un provider en échec est
déclaré en échec, pas remplacé par un faux résultat.

Discipline HTTP : ``httpx`` déjà présent, URLs construites uniquement
depuis des hôtes codés en dur (jamais depuis une saisie utilisateur) —
l'esprit anti-SSRF de ``webintel/security/`` est respecté sans
l'affaiblir.
"""

from webintel.providers.base import (
    BaseProvider,
    ProviderErreur,
    ResultatProvider,
)
from webintel.providers.registre import (
    rechercher_avec_fallback,
    statuts_providers,
)

__all__ = [
    "BaseProvider",
    "ProviderErreur",
    "ResultatProvider",
    "rechercher_avec_fallback",
    "statuts_providers",
]
