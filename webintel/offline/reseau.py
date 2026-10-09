"""Mode hors-ligne et dégradation gracieuse — WebIntel.

Le « mode hors-ligne » est un drapeau explicite (le terrain burkinabè
n'a souvent aucun réseau) :

- variable d'environnement ``WEBINTEL_HORS_LIGNE=1`` (lue à chaque
  appel, donc modifiable en test via ``monkeypatch``) ;
- ou bascule d'exécution ``definir_mode_hors_ligne(True/False)``
  (prioritaire sur la variable d'environnement ; page
  « Synchronisation » → section « Réseau »).

Quand le mode est actif :

- toute modification d'une entité synchronisable est enregistrée dans
  la file (``webintel.offline.synchronisation``) au lieu d'être
  poussée ;
- toute fonction dépendant du réseau (connecteurs externes PubChem /
  PubMed, tests de statut) lève ``ConnecteurIndisponible`` avec un
  message français explicite — SANS tenter d'accès réseau — et
  n'empêche jamais l'usage local ;
- l'UI affiche un bandeau « Mode hors-ligne » et distingue les
  « données locales » des « données synchronisées ».
"""

from __future__ import annotations

import os

MESSAGE_HORS_LIGNE = (
    "Mode hors-ligne actif : cette fonction nécessite un accès réseau "
    "(source externe). Aucune donnée n'a été récupérée et aucune tentative "
    "réseau n'a été effectuée. Vos données locales restent entièrement "
    "utilisables : plantes, collectes, calculs et rapports fonctionnent "
    "sans connexion."
)

# Bascule d'exécution (prioritaire sur la variable d'environnement).
# ``None`` = suivre ``WEBINTEL_HORS_LIGNE``.
_forcage: bool | None = None


def definir_mode_hors_ligne(actif: bool | None) -> None:
    """Bascule le mode hors-ligne à l'exécution.

    ``actif=True`` : force le mode hors-ligne ; ``False`` : force le
    mode en-ligne ; ``None`` : suit la variable d'environnement
    ``WEBINTEL_HORS_LIGNE``.
    """
    global _forcage
    _forcage = actif


def est_hors_ligne() -> bool:
    """Vrai si l'instance fonctionne en mode hors-ligne (terrain)."""
    if _forcage is not None:
        return _forcage
    return os.environ.get("WEBINTEL_HORS_LIGNE", "").strip().lower() in {
        "1",
        "true",
        "oui",
        "yes",
    }


def lever_si_hors_ligne(nom_fonction: str = "") -> None:
    """Lève ``ConnecteurIndisponible`` si le mode hors-ligne est actif.

    Utilisé par les connecteurs externes AVANT tout accès réseau :
    le message est explicite et en français, et l'usage local n'est
    jamais bloqué (l'appelant affiche l'erreur, typiquement HTTP 503
    côté API — comportement déjà en place).
    """
    if not est_hors_ligne():
        return
    from webintel.science.connecteurs.base import ConnecteurIndisponible

    precision = f" Fonction concernée : {nom_fonction}." if nom_fonction else ""
    raise ConnecteurIndisponible(MESSAGE_HORS_LIGNE + precision)
