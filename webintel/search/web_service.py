"""Service de recherche Web généraliste (v3.0.0).

Orchestration : compréhension de la requête (nettoyage borné) →
découverte via les providers avec fallback automatique →
normalisation/déduplication (faite par le registre) → retour structuré
``{requete, resultats, providers_utilises, providers_en_echec,
duree_ms}``. Aucun résultat n'est inventé : les échecs de providers
sont déclarés tels quels au consommateur (API, UI).
"""

from __future__ import annotations

from typing import Any

from webintel.providers.registre import rechercher_avec_fallback

_LIMITE_DEFAUT = 10
_LIMITE_MAX = 50


def normaliser_requete(requete: str) -> str:
    """Nettoie la requête brute : espaces normalisés, borne 300 caractères."""
    return " ".join((requete or "").split())[:300].strip()


def rechercher_web(
    requete: str,
    *,
    limite: int = _LIMITE_DEFAUT,
    langue: str = "fr",
    capacite: str | None = None,
) -> dict[str, Any]:
    """Recherche Web complète avec fallback entre providers.

    ``limite`` est bornée à [1, 50] ; ``langue`` vaut ``fr`` par défaut
    (``en`` accepté). Retourne le dict structuré du registre.
    """
    requete_propre = normaliser_requete(requete)
    limite_bornee = max(1, min(_LIMITE_MAX, int(limite)))
    langue_propre = "en" if (langue or "fr").lower().startswith("en") else "fr"
    resultat = rechercher_avec_fallback(
        requete_propre, limite=limite_bornee, langue=langue_propre, capacite=capacite
    )
    # La requête retournée est la forme normalisée réellement cherchée.
    resultat["requete"] = requete_propre
    resultat["langue"] = langue_propre
    resultat["limite"] = limite_bornee
    return resultat
