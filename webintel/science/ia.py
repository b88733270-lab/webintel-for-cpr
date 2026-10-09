"""Fonctionnalités d'IA de WebIntel — Phase 11 (entièrement désactivables).

Règles d'honnêteté scientifique (non négociables) :

- Ce module ne contient **aucun modèle génératif externe** : la « synthèse »
  est un **assemblage déterministe** de phrases construites à partir des
  preuves (``Evidence``) et des liens plante-composé déjà enregistrés. Aucune
  information nouvelle n'est inventée ; chaque affirmation cite sa source.
- Le résultat porte **toujours** le niveau de donnée ``hypothese`` (jamais
  ``publie``), est **toujours** accompagné de son avertissement, et n'est
  **jamais persisté** : une synthèse générée ne devient jamais un fait.
- Les données privées du laboratoire (``ResearchObservation``) ne sont
  **jamais** utilisées pour la synthèse.
- Le garde-fou global ``WEBINTEL_AI_ENABLED`` (+ case « Paramètres » de
  l'interface, voir ``webintel/config/runtime.py``) coupe TOUT : quand l'IA
  est désactivée, ``synthese_plante()`` lève ``IaDesactiveeError`` AVANT tout
  accès aux données, l'API répond 503, et l'interface ne montre plus aucune
  fonction IA (pas de bouton fantôme, pas d'appel silencieux).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from webintel.config.runtime import is_ai_enabled
from webintel.core.exceptions import WebIntelError
from webintel.science import fiche as fiche_module
from webintel.science.honnetete import _exiger_honnete
from webintel.science.models import Evidence, Plant, PlantCompound

AVERTISSEMENT_IA = (
    "Texte généré automatiquement par assemblage des sources citées ci-dessous. "
    "Il s'agit d'une HYPOTHÈSE de travail, pas d'une preuve scientifique : "
    "vérifiez chaque affirmation dans les sources avant toute utilisation. "
    "Aucune information nouvelle n'a été créée par un modèle ; les données "
    "privées du laboratoire n'ont pas été utilisées."
)

MOTEUR_IA = "assembleur_local_v1"


class IaDesactiveeError(WebIntelError):
    """Levée quand une fonctionnalité d'IA est appelée alors qu'elle est coupée."""

    def __init__(self) -> None:
        super().__init__(
            "Fonctionnalité d'IA désactivée : activez-la via la variable "
            "d'environnement WEBINTEL_AI_ENABLED ou la page « Paramètres » "
            "de l'interface pour l'utiliser."
        )


def verifier_ia_activee(settings=None) -> None:
    """Garde-fou : lève ``IaDesactiveeError`` si l'IA est désactivée.

    Appelé EN PREMIER par toute fonctionnalité d'IA, avant tout accès
    aux données ou tout calcul.
    """
    active, _source = is_ai_enabled(settings)
    if not active:
        raise IaDesactiveeError()


def _source_evidence(preuve: Evidence) -> dict:
    """Vue source d'une preuve (même forme que la fiche, niveau publié)."""
    return {
        "evidence_id": preuve.id,
        "type_source": preuve.source_type,
        "reference": preuve.reference,
        "doi": preuve.doi,
        "pmid": preuve.pmid,
        "url": preuve.url,
        "titre": preuve.title,
        "auteurs": list(preuve.authors or []),
        "annee": preuve.year,
        "extrait": preuve.excerpt,
        "data_level": "publie",
    }


def synthese_plante(db: Session, plant_id: int) -> dict:
    """Assemble une synthèse des connaissances sur une plante.

    Sources utilisées : les ``Evidence`` liées à la plante (même règle de
    liaison que la fiche : DOI commun avec un lien ``PlantCompound``) et les
    liens plante-composé enregistrés. Les observations privées du laboratoire
    sont EXCLUES.

    Le résultat est éphémère (jamais persisté en base) et étiqueté
    ``niveau_donnees = "hypothese"`` avec ``avertissement`` obligatoire.

    Lève ``KeyError`` si la plante est introuvable, ``IaDesactiveeError``
    si l'IA est désactivée (vérification faite avant toute requête).
    """
    verifier_ia_activee()

    plante = db.get(Plant, plant_id)
    if plante is None:
        raise KeyError(f"Plante introuvable : id={plant_id}")

    liens = (
        db.query(PlantCompound)
        .filter(PlantCompound.plant_id == plant_id)
        .order_by(PlantCompound.id)
        .all()
    )
    dois_lies = {lien.doi for lien in liens if lien.doi}
    preuves = (
        db.query(Evidence)
        .filter(Evidence.doi.in_(dois_lies))
        .order_by(Evidence.id)
        .all()
        if dois_lies
        else []
    )

    sources = [_source_evidence(p) for p in preuves]

    # Noms des composés liés (aucun lien inventé : uniquement les lignes existantes).
    from webintel.science.models import Compound  # import local : évite tout cycle

    ids_composes = sorted({lien.compound_id for lien in liens})
    composes = (
        db.query(Compound).filter(Compound.id.in_(ids_composes)).order_by(Compound.name).all()
        if ids_composes
        else []
    )
    noms_composes = [c.name for c in composes]

    parties = sorted({lien.plant_part for lien in liens if lien.plant_part})

    phrases: list[str] = []
    # Segments générés par WebIntel soumis à l'auto-contrôle d'honnêteté :
    # les verbatims sourcés (extraits, références cités avec leur source)
    # n'en font PAS partie — ce ne sont pas des affirmations du logiciel.
    controle: list[str] = []
    phrases.append(
        f"« {plante.scientific_name} » est une plante étudiée dans WebIntel : "
        f"{len(liens)} lien(s) plante-composé enregistré(s) et {len(preuves)} "
        f"preuve(s) documentaire(s) liée(s)."
    )
    controle.append(phrases[-1])
    if noms_composes:
        phrases.append(
            "Les composés rapportés dans les sources enregistrées sont : "
            + ", ".join(noms_composes)
            + "."
        )
        controle.append(phrases[-1])
    if parties:
        phrases.append("Parties de la plante mentionnées : " + ", ".join(parties) + ".")
        controle.append(phrases[-1])
    for preuve in preuves:
        citation = preuve.reference or preuve.title or f"preuve #{preuve.id}"
        if preuve.doi:
            citation += f" (DOI {preuve.doi})"
        extrait = (preuve.excerpt or "").strip()
        if extrait:
            phrases.append(f"Selon « {citation} » : « {extrait[:300]} ».")
            # Le texte cité appartient à la source, pas à WebIntel :
            # seul le squelette généré est contrôlé.
            controle.append(
                "Selon la source citée ci-dessus : [extrait de la source, "
                "reproduit tel quel — affirmation de la source, pas de WebIntel]."
            )
        else:
            phrases.append(f"Source citée : « {citation} ».")
            controle.append(
                "Source citée [référence de la source — mention "
                "documentaire, pas une affirmation de WebIntel]."
            )
    if not preuves:
        phrases.append(
            "Aucune preuve documentaire n'est actuellement liée à cette plante : "
            "cette synthèse reste vide tant qu'aucune source n'est enregistrée."
        )
        controle.append(phrases[-1])
    phrases.append(
        "Cette synthèse n'est qu'un assemblage automatique des sources ci-dessus : "
        "elle ne prouve rien et ne remplace pas la lecture des sources."
    )
    controle.append(phrases[-1])

    _exiger_honnete(
        "\n\n".join(controle + [AVERTISSEMENT_IA]),
        "synthèse IA",
    )

    return {
        "plante_id": plant_id,
        "nom_scientifique": plante.scientific_name,
        "titre": f"Synthèse automatique — {plante.scientific_name}",
        "texte": "\n\n".join(phrases),
        "sources": sources,
        "niveau_donnees": "hypothese",
        "badge": fiche_module._badge("hypothese"),
        "moteur": MOTEUR_IA,
        "avertissement": AVERTISSEMENT_IA,
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "persiste": False,
    }
