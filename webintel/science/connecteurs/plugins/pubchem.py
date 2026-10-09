"""Plugin PubChem (PUG-REST) — connecteur de source externe.

Source : PubChem, U.S. National Library of Medicine (domaine public),
sans clé d'API, lecture seule.

Limites documentées :
- PUG-REST est un service partagé : requêtes unitaires, espacées ;
  pas de moissonnage massif (respecter les CGU NCBI, débit raisonnable) ;
- un seul composé importé par appel (recherche par nom) ;
- les données importées sont NORMALISEES (niveau ``normalise``),
  jamais « scientifiquement validées ».
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from webintel.science import service as science_service
from webintel.science.connecteurs.base import (
    BaseConnecteur,
    ParametresInvalides,
)
from webintel.science.connecteurs.provenance import enregistrer_provenance

_PROPRIETES = (
    "MolecularFormula,MolecularWeight,CanonicalSMILES,"
    "IsomericSMILES,InChIKey"
)


class PubChemConnecteur(BaseConnecteur):
    """Connecteur PubChem PUG-REST : propriétés d'un composé par son nom."""

    NOM = "pubchem"
    DESCRIPTION = (
        "PubChem (NCBI) : propriétés chimiques d'un composé "
        "(formule, masse, SMILES, InChIKey) à partir de son nom."
    )
    URL_SOURCE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
    LICENCE = "PubChem — domaine public (U.S. National Library of Medicine)"
    LICENCE_URL = "https://pubchem.ncbi.nlm.nih.gov/docs/about"
    CONDITIONS_UTILISATION = (
        "Lecture seule, sans clé d'API. Requêtes unitaires espacées ; "
        "pas de moissonnage massif. Respecter les CGU NCBI "
        "(https://www.ncbi.nlm.nih.gov/home/about/policies/)."
    )
    VERSION = "1.0"
    STRATEGIE_MAJ = (
        "À la demande : chaque import interroge PUG-REST en direct "
        "(avec cache 24 h) et met à jour le composé existant."
    )

    VERSION_DONNEES = "PUG-REST"

    # CGU NCBI : ≤ 3 requêtes/s sans clé — requêtes espacées.
    delai_minimum_s = 0.34

    def _url_proprietes(self, nom: str) -> str:
        return (
            f"{self.URL_SOURCE}/compound/name/{quote(nom)}"
            f"/property/{_PROPRIETES}/JSON"
        )

    def tester_statut(self) -> tuple[bool, str]:
        """Teste PUG-REST via une requête légère (eau, CID 962)."""
        from webintel.core.exceptions import SecurityError
        from webintel.offline.reseau import MESSAGE_HORS_LIGNE, est_hors_ligne
        from webintel.security.pinned_transport import requete_sure

        if est_hors_ligne():
            return False, MESSAGE_HORS_LIGNE
        try:
            reponse = requete_sure(
                "GET",
                f"{self.URL_SOURCE}/compound/cid/962/property/"
                "MolecularFormula/JSON",
                timeout=self.timeout_s,
                autoriser_hote_science=True,
                headers={"User-Agent": "WebIntel-recherche"},
            )
            reponse.raise_for_status()
            return True, "PubChem PUG-REST joignable."
        except SecurityError as exc:
            return False, f"PubChem bloqué par la protection anti-SSRF : {exc}."
        except httpx.RequestError as exc:
            return False, f"PubChem injoignable : {exc}."
        except httpx.HTTPStatusError as exc:
            return False, f"PubChem répond en erreur : {exc}."

    def importer(
        self, session: Session, parametres: dict
    ) -> dict[str, Any]:
        """Importe les propriétés d'un composé depuis PubChem.

        Paramètres : ``{"nom": "<nom du composé>"}`` (obligatoire).

        Idempotence : le composé est créé/mis à jour par son nom
        canonique PubChem ; un réimport ne crée aucun doublon
        (InChIKey unique). Ne committe jamais.
        """
        nom = (parametres or {}).get("nom")
        if not nom or not str(nom).strip():
            raise ParametresInvalides(
                "Paramètre « nom » obligatoire : "
                'ex. {"nom": "aspirine"}.'
            )
        nom = str(nom).strip()

        rapport: dict[str, Any] = {
            "crees": 0,
            "mis_a_jour": 0,
            "ignores": 0,
            "erreurs": [],
            # Provenance standard (base) + clé historique version_donnees.
            "provenance": {
                **self.construire_provenance_import(
                    identifiant_externe=f"PUG-REST:{nom}"
                ),
                "version_donnees": self.VERSION_DONNEES,
            },
        }

        try:
            donnees = self.requete_json(session, self._url_proprietes(nom))
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                rapport["ignores"] = 1
                rapport["erreurs"].append(
                    f"Composé « {nom} » introuvable dans PubChem."
                )
                return rapport
            raise

        proprietes = (donnees.get("PropertyTable") or {}).get(
            "Properties", []
        )
        if not proprietes:
            rapport["ignores"] = 1
            rapport["erreurs"].append(
                f"PubChem ne retourne aucune propriété pour « {nom} »."
            )
            return rapport
        props = proprietes[0]

        nom_canonique = props.get("Title") or nom
        try:
            masse = props.get("MolecularWeight")
            masse = float(masse) if masse is not None else None
        except (TypeError, ValueError):
            masse = None

        champs = {
            "formula": props.get("MolecularFormula"),
            "molecular_weight": masse,
            "smiles": props.get("CanonicalSMILES"),
            "inchi": None,  # InChI complet non fourni par cet endpoint
            "inchikey": props.get("InChIKey"),
            "external_ids": {
                "pubchem": {
                    "isomeric_smiles": props.get("IsomericSMILES"),
                    "source": "PUG-REST",
                    "url": self._url_proprietes(nom),
                    "licence": self.LICENCE,
                },
                "connecteur": self.NOM,
                "niveau_donnees": "normalise",
            },
            "chemical_class": None,
        }
        try:
            compose, cree = science_service.create_compound(
                session, nom_canonique, **champs
            )
        except ValueError as exc:
            rapport["erreurs"].append(str(exc))
            return rapport

        if cree:
            rapport["crees"] = 1
        else:
            # Mise à jour sélective si les propriétés ont changé.
            modifie = False
            for attr, valeur in (
                ("formula", champs["formula"]),
                ("molecular_weight", champs["molecular_weight"]),
                ("smiles", champs["smiles"]),
                ("inchikey", champs["inchikey"]),
            ):
                if valeur is not None and getattr(compose, attr) != valeur:
                    setattr(compose, attr, valeur)
                    modifie = True
            session.flush()
            rapport["mis_a_jour"] = 1 if modifie else 0
            if not modifie:
                rapport["ignores"] = 1

        enregistrer_provenance(
            session,
            type_donnees="compound",
            item_id=compose.id,
            connecteur_nom=self.NOM,
            version_donnees=self.VERSION_DONNEES,
            licence=self.LICENCE,
        )
        return rapport
