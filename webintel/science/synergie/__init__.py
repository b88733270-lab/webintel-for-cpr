"""Répartiteur des méthodes d'analyse de synergie (MODE B).

GARDE-FOU CENTRAL : ``analyser_synergie`` refuse toute analyse dont les
données d'entrée ne contiennent pas de mesures expérimentales réelles
(``donnees`` sans points de mesure → ``DonneesInsuffisantes``). Il est
notamment INTERDIT de produire un score de synergie à partir de simples
structures moléculaires (SMILES seuls) : une telle tentative lève une
erreur explicite, jamais un calcul.
"""

from __future__ import annotations

from . import bliss, hsa, loewe, zip as zip_mod
from .commun import DonneesInsuffisantes

METHODES = {
    "bliss": bliss,
    "hsa": hsa,
    "loewe": loewe,
    "zip": zip_mod,
}


def methodes_disponibles() -> list[dict]:
    """Fiches des 4 méthodes (nom, formule, données requises, référence)."""
    return [
        {
            "nom": nom,
            "formule": module.FORMULE,
            "donnees_requises": module.donnees_requises(),
            "reference": module.analyser.__doc__ or "",
        }
        for nom, module in METHODES.items()
    ]


def _a_des_mesures(donnees: dict) -> bool:
    """Vrai si ``donnees`` contient au moins une mesure expérimentale réelle.

    Garde-fou : des SMILES, des noms de molécules ou des descripteurs
    seuls ne sont PAS des mesures — l'analyse est refusée dans ce cas.
    """
    if not isinstance(donnees, dict):
        return False
    for cle in ("points", "melanges"):
        serie = donnees.get(cle)
        if isinstance(serie, list) and any(isinstance(p, dict) for p in serie):
            return True
    return False


def analyser_synergie(
    methode: str, donnees: dict, parametres: dict | None = None
) -> dict:
    """Exécute une méthode de synergie sur des données expérimentales.

    - ``methode`` inconnue → ``ValueError`` explicite ;
    - données sans mesures réelles (ex. simples SMILES) →
      ``DonneesInsuffisantes`` (« données insuffisantes pour la méthode
      X ») — JAMAIS de score inventé ;
    - données incomplètes pour la méthode → ``DonneesInsuffisantes``.
    """
    nom = (methode or "").strip().lower()
    module = METHODES.get(nom)
    if module is None:
        raise ValueError(
            f"Méthode de synergie inconnue : « {methode} ». "
            f"Méthodes disponibles : {', '.join(sorted(METHODES))}."
        )
    if not _a_des_mesures(donnees or {}):
        raise DonneesInsuffisantes(
            f"données insuffisantes pour la méthode {nom} : aucune mesure "
            "expérimentale réelle fournie (molécule A, molécule B, dose A, "
            "dose B, réponse mesurée). Un score de synergie ne peut pas "
            "être calculé à partir de simples structures moléculaires."
        )
    return module.analyser(donnees or {}, parametres or {})
