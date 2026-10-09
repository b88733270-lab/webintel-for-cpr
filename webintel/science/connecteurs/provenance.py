"""Enregistrement de la provenance des imports — Phase 10.

CHEMIN ET SIGNATURE CONTRACTUELS : l'agent d'import utilise
``from webintel.science.connecteurs.provenance import
enregistrer_provenance``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from webintel.core.utils import utcnow
from webintel.science.models import ImportProvenance, Provenance

#: Champs canoniques de provenance d'une donnée locale — même dignité
#: qu'une donnée internationale : QUI / QUOI / QUAND / OÙ / SOURCE /
#: COMMENT / OUTIL / VERSION / PARAMÈTRES / NIVEAU DE PREUVE.
CHAMPS_PROVENANCE_LOCALE = (
    "qui",
    "quoi",
    "quand",
    "ou",
    "source",
    "commentaire",
    "outil",
    "version",
    "parametres",
    "niveau_preuve",
)


def enregistrer_provenance(
    session: Session,
    *,
    type_donnees: str,
    item_id: int,
    connecteur_nom: str,
    version_donnees: str | None = None,
    licence: str | None = None,
) -> ImportProvenance:
    """Crée une ligne de traçabilité pour un objet importé.

    Enregistre qui a fourni l'objet (``connecteur_nom``), quand
    (``date_import`` = maintenant, UTC), quelle version des données et
    sous quelle licence. Ne committe jamais (l'appelant décide).
    """
    ligne = ImportProvenance(
        type_donnees=type_donnees,
        item_id=item_id,
        connecteur_nom=connecteur_nom,
        date_import=utcnow(),
        version_donnees=version_donnees,
        licence=licence,
    )
    session.add(ligne)
    session.flush()
    return ligne


def enregistrer_provenance_detaillee(
    session: Session,
    *,
    entity_type: str,
    entity_id: int,
    qui: str | None = None,
    quand: Any = None,
    ou: str | None = None,
    source: str | None = None,
    commentaire: str | None = None,
    outil: str | None = None,
    version: str | None = None,
    parametres: dict | None = None,
    niveau_preuve: str | None = None,
    connecteur: str | None = None,
    licence: str | None = None,
) -> Provenance:
    """Enregistre la provenance complète d'une donnée LOCALE.

    Les 10 champs canoniques (QUI/QUOI/QUAND/OÙ/SOURCE/COMMENT/OUTIL/
    VERSION/PARAMÈTRES/NIVEAU DE PREUVE) : une donnée de chercheur,
    de laboratoire, d'université ou de terrain a la même dignité de
    provenance qu'une donnée internationale. Journal append-only : on
    n'écrase jamais une ligne existante. Ne committe jamais.
    """
    if isinstance(quand, str):
        try:
            quand = datetime.fromisoformat(quand)
        except ValueError:
            quand = utcnow()
    ligne = Provenance(
        entity_type=entity_type,
        entity_id=entity_id,
        source_name=source,
        connector=connecteur,
        data_version=version,
        license=licence,
        imported_at=quand or utcnow(),
        acteur=qui,
        lieu=ou,
        commentaire=commentaire,
        outil=outil,
        parametres=parametres,
        niveau_preuve=niveau_preuve,
    )
    session.add(ligne)
    session.flush()
    return ligne


def normaliser_provenance_locale(provenance: dict | None) -> dict[str, Any]:
    """Normalise un dict de provenance locale sur les 10 champs canoniques.

    Les clés inconnues sont conservées (jamais de perte d'information) ;
    les champs canoniques absents valent ``None``. Fonction pure.
    """
    provenance = dict(provenance or {})
    normalisee: dict[str, Any] = {
        champ: provenance.get(champ) for champ in CHAMPS_PROVENANCE_LOCALE
    }
    for cle, valeur in provenance.items():
        if cle not in normalisee:
            normalisee[cle] = valeur
    # « quoi » : l'entité décrite, si fournie sous un autre nom.
    if normalisee["quoi"] is None:
        normalisee["quoi"] = provenance.get("entite") or provenance.get("objet")
    return normalisee
