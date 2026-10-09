"""Plugin GBIF — connecteur de source externe.

Source : GBIF — Global Biodiversity Information Facility
(https://www.gbif.org), **sans clé d'API**, lecture seule.

Licence : les métadonnées taxonomiques sont des faits publics ; les
**occurrences** portent chacune la licence de leur jeu de données
(CC0 / CC-BY / CC-BY-NC — champ ``license`` de l'API). Ce connecteur
n'importe QUE des métadonnées taxonomiques et des COMPTES
d'occurrences (pas de fiches d'occurrence) : aucune licence par
enregistrement n'est donc due. Toute future importation de fiches
d'occurrence devra enregistrer la licence de chaque jeu de données
source.

Limites documentées :
- requêtes espacées (courtoisie : ~1/s) ; pas de moissonnage massif ;
- un taxon par appel (nom scientifique) ;
- les données importées sont NORMALISEES (niveau ``normalise``) ;
- un nom synonyme est signalé (statut GBIF + nom accepté proposés),
  jamais « corrigé » silencieusement : le chercheur décide.
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

_STATUTS_AMBIGUS = {"SYNONYM", "DOUBTFUL", "MISAPPLIED"}


class GBIFConnecteur(BaseConnecteur):
    """Connecteur GBIF : vérification taxonomique + occurrences."""

    NOM = "gbif"
    DESCRIPTION = (
        "GBIF : vérification d'un nom scientifique (statut "
        "taxonomique, rang, nom accepté) et comptage d'occurrences "
        "par pays — utile pour documenter la répartition d'une "
        "plante médicinale."
    )
    URL_SOURCE = "https://api.gbif.org/v1"
    LICENCE = (
        "GBIF — métadonnées taxonomiques publiques ; occurrences : "
        "licence propre à chaque jeu de données (CC0 / CC-BY / CC-BY-NC)"
    )
    LICENCE_URL = "https://www.gbif.org/terms"
    CONDITIONS_UTILISATION = (
        "Lecture seule, sans clé d'API. Requêtes espacées (~1/s, "
        "courtoisie) ; pas de moissonnage massif. Seuls des "
        "métadonnées et des comptes sont importés (aucune fiche "
        "d'occurrence recopiée)."
    )
    VERSION = "1.0"
    STRATEGIE_MAJ = (
        "À la demande : chaque import interroge l'API GBIF en direct "
        "(avec cache 24 h) et met à jour la fiche taxonomique de la "
        "plante."
    )

    VERSION_DONNEES = "GBIF API v1"
    delai_minimum_s = 1.0

    def _apparier(
        self, session: Session, nom: str
    ) -> dict | None:
        donnees = self.requete_json(
            session,
            f"{self.URL_SOURCE}/species/match",
            {"name": nom, "verbose": "true"},
        )
        if donnees.get("matchType") in (None, "NONE"):
            return None
        return donnees

    def _nom_accepte(
        self, session: Session, cle_acceptee: int
    ) -> str | None:
        try:
            fiche = self.requete_json(
                session, f"{self.URL_SOURCE}/species/{cle_acceptee}", None
            )
            return fiche.get("canonicalName")
        except Exception:  # noqa: BLE001 — informatif, jamais bloquant
            return None

    def _compter_occurrences(
        self, session: Session, cle_taxon: int, pays: str | None
    ) -> tuple[int | None, int | None]:
        """(occurrences dans le pays, occurrences totales) — comptes seuls."""
        def _compte(params: dict) -> int | None:
            try:
                donnees = self.requete_json(
                    session,
                    f"{self.URL_SOURCE}/occurrence/search",
                    {**params, "limit": "0"},
                )
                return donnees.get("count")
            except Exception:  # noqa: BLE001 — informatif
                return None

        total = _compte({"taxon_key": str(cle_taxon)})
        par_pays = _compte({"taxon_key": str(cle_taxon), "country": pays}) if pays else None
        return par_pays, total

    def tester_statut(self) -> tuple[bool, str]:
        """Teste l'API GBIF via un appariement minimal."""
        import httpx

        from webintel.core.exceptions import SecurityError
        from webintel.security.pinned_transport import requete_sure

        try:
            reponse = requete_sure(
                "GET",
                f"{self.URL_SOURCE}/species/match",
                params={"name": "Quercus robur"},
                timeout=self.timeout_s,
                autoriser_hote_science=True,
                headers={"User-Agent": "WebIntel-recherche"},
            )
            reponse.raise_for_status()
            return True, "GBIF joignable."
        except SecurityError as exc:
            return False, f"GBIF bloqué par la protection anti-SSRF : {exc}."
        except httpx.RequestError as exc:
            return False, f"GBIF injoignable : {exc}."
        except httpx.HTTPStatusError as exc:
            return False, f"GBIF répond en erreur : {exc}."

    def importer(
        self, session: Session, parametres: dict
    ) -> dict[str, Any]:
        """Vérifie un nom scientifique via GBIF et met à jour la plante.

        Paramètres : ``{"nom_scientifique": "<nom>", "pays": "<code ISO>",
        "max": <int>}`` — seul ``nom_scientifique`` est obligatoire ;
        ``pays`` (ex. ``"BF"``) borne le comptage d'occurrences.

        Idempotence : plante retrouvée/créée par son nom canonique
        GBIF. Ne committe jamais.
        """
        params = parametres or {}
        nom = (params.get("nom_scientifique") or "").strip()
        if not nom:
            raise ParametresInvalides(
                "Paramètre « nom_scientifique » obligatoire : "
                "'ex. {\"nom_scientifique\": \"Moringa oleifera\", "
                "\"pays\": \"BF\"}.'"
            )
        pays = (params.get("pays") or "").strip().upper() or None
        if pays and (len(pays) != 2 or not pays.isalpha()):
            raise ParametresInvalides(
                f"Paramètre « pays » invalide : {pays!r} "
                "(code ISO à 2 lettres attendu, ex. « BF »)."
            )

        rapport: dict[str, Any] = {
            "crees": 0,
            "mis_a_jour": 0,
            "ignores": 0,
            "erreurs": [],
            "avertissements": [],
            "provenance": {
                **self.construire_provenance_import(
                    identifiant_externe=f"GBIF:{nom}"
                ),
                "version_donnees": self.VERSION_DONNEES,
            },
        }

        appariement = self._apparier(session, nom)
        if appariement is None:
            rapport["ignores"] = 1
            rapport["erreurs"].append(
                f"Aucun taxon GBIF pour « {nom} »."
            )
            return rapport

        statut = appariement.get("status")
        cle_taxon = appariement.get("usageKey")
        nom_canonique = appariement.get("canonicalName") or nom
        nom_accepte = None
        if statut in _STATUTS_AMBIGUS:
            cle_acceptee = appariement.get("acceptedUsageKey")
            if cle_acceptee:
                nom_accepte = self._nom_accepte(session, cle_acceptee)

        par_pays, total = self._compter_occurrences(session, cle_taxon, pays)

        infos_gbif = {
            "taxon_key": cle_taxon,
            "nom_apparie": appariement.get("scientificName"),
            "nom_canonique": nom_canonique,
            "rang": appariement.get("rank"),
            "statut": statut,
            "type_appariement": appariement.get("matchType"),
            "confiance": appariement.get("confidence"),
            "nom_accepte": nom_accepte,
            "occurrences_pays": par_pays,
            "occurrences_total": total,
            "code_pays": pays,
            "url": f"https://www.gbif.org/species/{cle_taxon}",
            "licence_note": (
                "Métadonnées taxonomiques publiques ; comptes "
                "d'occurrences sans recopie de fiches (licences "
                "par jeu de données non applicables)."
            ),
        }

        plante, creee = science_service.create_plant(session, nom_canonique)
        taxonomie = dict(plante.taxonomy or {})
        for cle in (
            "kingdom", "phylum", "class", "order", "family", "genus", "species",
        ):
            valeur = appariement.get(cle)
            if valeur:
                taxonomie[f"gbif_{cle}"] = valeur
        plante.taxonomy = taxonomie
        if not plante.family and appariement.get("family"):
            plante.family = appariement["family"]
        if not plante.genus and appariement.get("genus"):
            plante.genus = appariement["genus"]
        ids_externes = dict(plante.external_ids or {})
        ids_externes["gbif"] = infos_gbif
        plante.external_ids = ids_externes
        session.flush()

        if creee:
            rapport["crees"] = 1
        else:
            rapport["mis_a_jour"] = 1
        if statut in _STATUTS_AMBIGUS:
            precision = (
                f" — nom accepté : « {nom_accepte} »" if nom_accepte else ""
            )
            rapport["avertissements"].append(
                f"GBIF classe « {nom} » comme « {statut} »{precision}. "
                "Le nom saisi est conservé tel quel : vérifiez et adoptez "
                "le nom accepté si pertinent (contrôle qualité : "
                "« taxons ambigus »)."
            )
        if appariement.get("confidence") is not None and appariement["confidence"] < 80:
            rapport["avertissements"].append(
                f"Confiance d'appariement faible "
                f"({appariement['confidence']} %) : vérifiez le nom saisi."
            )

        enregistrer_provenance(
            session,
            type_donnees="plant",
            item_id=plante.id,
            connecteur_nom=self.NOM,
            version_donnees=self.VERSION_DONNEES,
            licence=self.LICENCE,
        )
        return rapport
