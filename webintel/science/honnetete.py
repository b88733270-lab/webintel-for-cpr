"""Garde-fou central d'honnêteté scientifique (v2.2.0).

RÈGLE PRODUIT (non négociable) : WebIntel ne présente jamais un résultat
informatique comme une preuve, et ne qualifie jamais un composé, une
plante ou un mélange d'« efficace », « sûr », « traite », « guérit » ou
« soigne » sans source expérimentale ou clinique appropriée.

Ce module centralise :
- ``FORMULATIONS_INTERDITES`` : la regex des formulations bannies des
  textes GÉNÉRÉS par WebIntel (même dans une négation, par prudence) ;
- ``verifier_honnetete`` / ``_exiger_honnete`` : l'auto-contrôle bloquant
  (``RuntimeError`` plutôt qu'un rapport non conforme) ;
- la distinction TEXTE GÉNÉRÉ vs VERBATIM SOURCÉ : les extraits de
  publications, titres, références et résultats rapportés sont des
  DONNÉES citées avec leur source — jamais des affirmations de WebIntel.
  Ils sont donc EXCLUS du scan bloquant (``CLES_VERBATIM``), tout en
  restant affichés avec leur source.

Tous les textes générés (études de combinaison, docking, rapports,
synthèse IA, fiche exportée, UI) doivent passer par ce module.
"""

from __future__ import annotations

import re

# Racines et formes bannies des textes générés (même dans une négation,
# par prudence). Les motifs verbaux utilisent des frontières de mots pour
# ne pas bloquer « traitement », « traité », « soigner », « guérison »…
FORMULATIONS_INTERDITES = re.compile(
    r"efficac"                  # efficace, efficacité…
    r"|sûr"                     # sûr (jamais « sûr d'emploi »)
    r"|innocu"                  # innocuité…
    r"|\btraite(?:nt)?\b"       # traite, traitent (verbe) — pas « traitement »
    r"|\bgu[eé]ri(?:e|s|t)?\b"  # guéri, guérie, guéris, guérit — pas « guérison »
    r"|\bsoign(?:e|ent)\b",     # soigne, soignent (verbe) — pas « soigner »
    re.IGNORECASE,
)

#: Clés JSON considérées comme des verbatims sourcés (données citées avec
#: leur source, jamais des affirmations générées par WebIntel). Leurs
#: valeurs sont EXCLUES du scan bloquant.
CLES_VERBATIM = frozenset({
    "titre",            # titre d'une publication / d'une preuve
    "reference",        # référence bibliographique citée
    "extrait",          # extrait de publication cité
    "excerpt",          # idem (forme anglaise)
    "publication",      # publication déclarée d'un lien
    "citation",         # citation d'une source
    "published_result", # résultat rapporté (donnée utilisateur/labo)
    "resultat_publie",  # idem (forme française)
    "bibliographie",    # liste de citations de sources
    "verbatim",         # verbatim d'enquête ou de source
    "references",       # liste de citations (rapport, section littérature)
    "texte",            # texte d'hypothèse fourni par le chercheur
})


def verifier_honnetete(texte: str) -> list[str]:
    """Retourne les formulations interdites trouvées dans ``texte``.

    Liste vide = conforme. Utilisé en auto-contrôle avant persistance
    et par les tests automatisés.
    """
    if not texte:
        return []
    return sorted(set(m.group(0) for m in FORMULATIONS_INTERDITES.finditer(texte)))


def _exiger_honnete(texte: str, contexte: str) -> None:
    """Auto-contrôle bloquant sur un texte généré (RuntimeError si non conforme)."""
    violations = verifier_honnetete(texte)
    if violations:
        raise RuntimeError(
            f"Rapport non conforme ({contexte}) : formulations interdites "
            f"détectées : {', '.join(violations)}. Le rapport n'a pas été produit."
        )


def texte_genere(objet: object) -> str:
    """Extrait le texte GÉNÉRÉ par WebIntel d'une structure (dict/list/str).

    Les valeurs portées par les clés de ``CLES_VERBATIM`` (extraits,
    titres, références, résultats rapportés…) sont des données sourcées
    citées avec leur source : elles sont exclues du texte retourné.
    """
    morceaux: list[str] = []

    def _visite(noeud: object) -> None:
        if isinstance(noeud, dict):
            for cle, valeur in noeud.items():
                if cle in CLES_VERBATIM:
                    continue
                _visite(valeur)
        elif isinstance(noeud, (list, tuple)):
            for item in noeud:
                _visite(item)
        elif isinstance(noeud, str):
            morceaux.append(noeud)

    _visite(objet)
    return "\n".join(morceaux)


def exiger_dossier_honnete(dossier: dict, contexte: str) -> None:
    """Auto-contrôle bloquant d'un dossier/rapport structuré.

    Seul le texte généré par WebIntel est scanné ; les verbatims sourcés
    (extraits, titres, références cités avec leur source) n'entraînent
    jamais de blocage — ils ne sont pas des affirmations du logiciel.
    """
    _exiger_honnete(texte_genere(dossier), contexte)
