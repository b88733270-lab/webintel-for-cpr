"""Plugin ChEMBL — connecteur de source externe.

Source : ChEMBL, EMBL-EBI (https://www.ebi.ac.uk/chembl) — base de
données de molécules bioactives, **sans clé d'API**, lecture seule.

Licence vérifiée : les données ChEMBL sont publiées sous **CC-BY-SA
3.0** (https://www.ebi.ac.uk/chembl/) — attribution obligatoire :
toute donnée importée cite ChEMBL comme source.

Limites documentées :
- requêtes unitaires espacées (service partagé EBI) ; pas de
  moissonnage massif ;
- un seul composé par appel (recherche par nom préféré ou identifiant
  ChEMBL) ;
- les molécules importées sont NORMALISEES (niveau ``normalise``),
  jamais « scientifiquement validées » ;
- le nombre d'activités est un COMPTE informatif (pas d'import
  d'activités : les valeurs expérimentales restent chez ChEMBL).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from webintel.science import service as science_service
from webintel.science.connecteurs.base import (
    BaseConnecteur,
    ParametresInvalides,
)
from webintel.science.connecteurs.provenance import enregistrer_provenance


class ChEMBLConnecteur(BaseConnecteur):
    """Connecteur ChEMBL : propriétés d'une molécule bioactive."""

    NOM = "chembl"
    DESCRIPTION = (
        "ChEMBL (EMBL-EBI) : propriétés d'une molécule bioactive "
        "(formule, masse, SMILES, InChIKey, identifiant ChEMBL) à "
        "partir de son nom ou de son identifiant ChEMBL."
    )
    URL_SOURCE = "https://www.ebi.ac.uk/chembl/api/data"
    LICENCE = "ChEMBL — CC-BY-SA 3.0 (EMBL-EBI)"
    LICENCE_URL = "https://www.ebi.ac.uk/chembl/"
    CONDITIONS_UTILISATION = (
        "Lecture seule, sans clé d'API. Données sous CC-BY-SA 3.0 : "
        "attribution obligatoire (citer ChEMBL comme source). Requêtes "
        "unitaires espacées ; pas de moissonnage massif."
    )
    VERSION = "1.0"
    STRATEGIE_MAJ = (
        "À la demande : chaque import interroge l'API ChEMBL en direct "
        "(avec cache 24 h) et met à jour le composé existant."
    )

    VERSION_DONNEES = "ChEMBL webresource API"
    delai_minimum_s = 0.5

    def _rechercher_par_nom(
        self, session: Session, nom: str
    ) -> dict | None:
        """Recherche exacte (insensible à la casse) sur le nom préféré."""
        donnees = self.requete_json(
            session,
            f"{self.URL_SOURCE}/molecule.json",
            {"pref_name__iexact": nom, "limit": "2"},
        )
        molecules = donnees.get("molecules", []) or []
        if len(molecules) != 1:
            # Zéro ou plusieurs candidats : on refuse de deviner (jamais
            # d'import ambigu — le chercheur précise l'identifiant).
            return None
        return molecules[0]

    def _lire_molecule(
        self, session: Session, chembl_id: str
    ) -> dict | None:
        import httpx

        try:
            return self.requete_json(
                session,
                f"{self.URL_SOURCE}/molecule/{quote(chembl_id)}.json",
                None,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def _compter_activites(self, session: Session, chembl_id: str) -> int | None:
        """Nombre d'activités documentées (compte informatif uniquement)."""
        try:
            donnees = self.requete_json(
                session,
                f"{self.URL_SOURCE}/activity.json",
                {"molecule_chembl_id": chembl_id, "limit": "1"},
            )
            return (donnees.get("page_meta") or {}).get("total_count")
        except Exception:  # noqa: BLE001 — informatif, jamais bloquant
            return None

    def tester_statut(self) -> tuple[bool, str]:
        """Teste l'API ChEMBL via une lecture légère (quercétine)."""
        import httpx

        from webintel.core.exceptions import SecurityError
        from webintel.security.pinned_transport import requete_sure

        try:
            reponse = requete_sure(
                "GET",
                f"{self.URL_SOURCE}/molecule/CHEMBL50.json",
                timeout=self.timeout_s,
                autoriser_hote_science=True,
                headers={"User-Agent": "WebIntel-recherche"},
            )
            reponse.raise_for_status()
            return True, "ChEMBL joignable."
        except SecurityError as exc:
            return False, f"ChEMBL bloqué par la protection anti-SSRF : {exc}."
        except httpx.RequestError as exc:
            return False, f"ChEMBL injoignable : {exc}."
        except httpx.HTTPStatusError as exc:
            return False, f"ChEMBL répond en erreur : {exc}."

    def importer(
        self, session: Session, parametres: dict
    ) -> dict[str, Any]:
        """Importe une molécule depuis ChEMBL.

        Paramètres : ``{"nom": "<nom>"}`` ou ``{"chembl_id": "CHEMBL…"}``
        (l'un des deux obligatoire).

        Idempotence : InChIKey unique (comme PubChem). Ne committe jamais.
        """
        params = parametres or {}
        nom = (params.get("nom") or "").strip()
        chembl_id = (params.get("chembl_id") or "").strip().upper()
        if not nom and not chembl_id:
            raise ParametresInvalides(
                "Paramètre « nom » ou « chembl_id » obligatoire : "
                "'ex. {\"nom\": \"quercétine\"} ou "
                "{\"chembl_id\": \"CHEMBL50\"}.'"
            )

        rapport: dict[str, Any] = {
            "crees": 0,
            "mis_a_jour": 0,
            "ignores": 0,
            "erreurs": [],
            "avertissements": [],
            "provenance": {
                **self.construire_provenance_import(
                    identifiant_externe=chembl_id or f"ChEMBL:{nom}"
                ),
                "version_donnees": self.VERSION_DONNEES,
            },
        }

        if chembl_id:
            molecule = self._lire_molecule(session, chembl_id)
            if molecule is None:
                rapport["ignores"] = 1
                rapport["erreurs"].append(
                    f"Identifiant « {chembl_id} » introuvable dans ChEMBL."
                )
                return rapport
        else:
            molecule = self._rechercher_par_nom(session, nom)
            if molecule is None:
                rapport["ignores"] = 1
                rapport["erreurs"].append(
                    f"Aucune molécule ChEMBL au nom exact « {nom} » "
                    "(recherche insensible à la casse sur le nom préféré). "
                    "Précisez l'identifiant (ex. CHEMBL50) en cas "
                    "d'ambiguïté."
                )
                return rapport

        structures = molecule.get("molecule_structures") or {}
        proprietes = molecule.get("molecule_properties") or {}
        chembl_id = molecule.get("molecule_chembl_id")
        nom_prefere = molecule.get("pref_name") or nom or chembl_id
        try:
            masse = proprietes.get("full_mwt")
            masse = float(masse) if masse is not None else None
        except (TypeError, ValueError):
            masse = None
        nb_activites = self._compter_activites(session, chembl_id)

        champs = {
            "formula": proprietes.get("full_molformula"),
            "molecular_weight": masse,
            "smiles": structures.get("canonical_smiles"),
            "inchi": structures.get("standard_inchi"),
            "inchikey": structures.get("standard_inchi_key"),
            "external_ids": {
                "chembl": {
                    "chembl_id": chembl_id,
                    "pref_name": molecule.get("pref_name"),
                    "max_phase": molecule.get("max_phase"),
                    "nb_activites_documentees": nb_activites,
                    "url": (
                        f"https://www.ebi.ac.uk/chembl/compound_report_card/"
                        f"{chembl_id}/"
                    ),
                    "licence": self.LICENCE,
                },
                "connecteur": self.NOM,
                "niveau_donnees": "normalise",
            },
            "chemical_class": None,
        }
        try:
            compose, cree = science_service.create_compound(
                session, nom_prefere, **champs
            )
        except ValueError as exc:
            rapport["erreurs"].append(str(exc))
            return rapport

        if cree:
            rapport["crees"] = 1
        else:
            modifie = False
            for attr, valeur in (
                ("formula", champs["formula"]),
                ("molecular_weight", champs["molecular_weight"]),
                ("smiles", champs["smiles"]),
                ("inchi", champs["inchi"]),
                ("inchikey", champs["inchikey"]),
            ):
                if valeur is not None and getattr(compose, attr) != valeur:
                    setattr(compose, attr, valeur)
                    modifie = True
            # L'identifiant ChEMBL est toujours (ré)enregistré.
            ids = dict(compose.external_ids or {})
            ids["chembl"] = champs["external_ids"]["chembl"]
            compose.external_ids = ids
            session.flush()
            rapport["mis_a_jour"] = 1 if modifie else 0
            if not modifie:
                rapport["ignores"] = 1

        if nb_activites:
            rapport["avertissements"].append(
                f"ChEMBL documente {nb_activites} activité(s) pour {chembl_id} : "
                "seul le COMPTE est importé (valeurs expérimentales non "
                "recopiées — consultez la fiche ChEMBL)."
            )
        enregistrer_provenance(
            session,
            type_donnees="compound",
            item_id=compose.id,
            connecteur_nom=self.NOM,
            version_donnees=self.VERSION_DONNEES,
            licence=self.LICENCE,
        )
        return rapport
