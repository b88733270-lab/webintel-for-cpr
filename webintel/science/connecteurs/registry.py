"""Registre des connecteurs de sources externes — Phase 10.

``CONNECTEURS`` : nom → classe de connecteur.
``lister_connecteurs()`` : toutes les classes déclarées.
``get_connecteur(nom)`` : instance du connecteur (``ValueError``
explicite si inconnu).
``initialiser_connecteurs(session)`` : crée les lignes
``SourceConnecteur`` manquantes (idempotent, ne committe pas).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from webintel.science.connecteurs.base import BaseConnecteur
from webintel.science.connecteurs.plugins.pubchem import PubChemConnecteur
from webintel.science.connecteurs.plugins.pubmed import PubMedConnecteur
from webintel.science.connecteurs.plugins.openalex import OpenAlexConnecteur
from webintel.science.connecteurs.plugins.chembl import ChEMBLConnecteur
from webintel.science.connecteurs.plugins.gbif import GBIFConnecteur
from webintel.science.models import SourceConnecteur

CONNECTEURS: dict[str, type[BaseConnecteur]] = {
    PubChemConnecteur.NOM: PubChemConnecteur,
    PubMedConnecteur.NOM: PubMedConnecteur,
    OpenAlexConnecteur.NOM: OpenAlexConnecteur,
    ChEMBLConnecteur.NOM: ChEMBLConnecteur,
    GBIFConnecteur.NOM: GBIFConnecteur,
}


def lister_connecteurs() -> list[type[BaseConnecteur]]:
    """Retourne les classes de connecteurs déclarées."""
    return list(CONNECTEURS.values())


def get_connecteur(nom: str) -> BaseConnecteur:
    """Retourne une instance du connecteur ``nom``.

    Lève ``ValueError`` explicite si le nom est inconnu.
    """
    try:
        classe = CONNECTEURS[nom]
    except KeyError:
        connus = ", ".join(sorted(CONNECTEURS)) or "aucun"
        raise ValueError(
            f"Connecteur inconnu : « {nom} ». "
            f"Connecteurs disponibles : {connus}."
        ) from None
    return classe()


def initialiser_connecteurs(session: Session) -> int:
    """Crée les lignes ``SourceConnecteur`` manquantes (idempotent).

    Chaque connecteur déclaré obtient sa ligne avec licence, CGU et
    stratégie de mise à jour. Ne committe jamais (l'appelant décide).
    Retourne le nombre de lignes créées.
    """
    crees = 0
    for classe in lister_connecteurs():
        existant = (
            session.query(SourceConnecteur)
            .filter(SourceConnecteur.nom == classe.NOM)
            .one_or_none()
        )
        if existant is not None:
            continue
        session.add(
            SourceConnecteur(
                nom=classe.NOM,
                description=classe.DESCRIPTION,
                url=classe.URL_SOURCE,
                licence=classe.LICENCE,
                licence_url=classe.LICENCE_URL,
                conditions_utilisation=classe.CONDITIONS_UTILISATION,
                version_connecteur=classe.VERSION,
                statut="actif",
                strategie_maj=classe.STRATEGIE_MAJ,
            )
        )
        crees += 1
    session.flush()
    return crees
