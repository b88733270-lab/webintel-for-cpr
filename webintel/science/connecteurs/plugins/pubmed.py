"""Plugin PubMed (NCBI E-utilities) — connecteur de source externe.

Source : PubMed, U.S. National Library of Medicine (données publiques),
sans clé d'API, lecture seule. Les requêtes identifient l'outil
(``tool=WebIntel-recherche``) comme le demandent les CGU NCBI.

Limites documentées :
- requêtes unitaires, espacées (max 3/s sans clé selon les CGU NCBI) ;
  pas de moissonnage massif ;
- import sélectif : une requête → au plus ``max`` publications
  (défaut 5, plafond 20) ;
- chaque publication importée devient une ``Evidence`` idempotente
  sur PMID/DOI, avec provenance (licence, date, version des données).
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from webintel.core.utils import utcnow
from webintel.science import service as science_service
from webintel.science.connecteurs.base import (
    BaseConnecteur,
    ParametresInvalides,
)
from webintel.science.connecteurs.provenance import enregistrer_provenance
from webintel.science.litterature import trouver_evidence_existante

_MAX_RESULTATS = 20


class PubMedConnecteur(BaseConnecteur):
    """Connecteur PubMed : publications liées à une requête."""

    NOM = "pubmed"
    DESCRIPTION = (
        "PubMed (NCBI E-utilities) : références bibliographiques "
        "(titre, auteurs, année, PMID, DOI) pour une requête."
    )
    URL_SOURCE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    LICENCE = "PubMed — données publiques (U.S. National Library of Medicine)"
    LICENCE_URL = "https://www.ncbi.nlm.nih.gov/home/about/policies/"
    CONDITIONS_UTILISATION = (
        "Lecture seule, sans clé d'API ; paramètre tool=WebIntel-recherche. "
        "Requêtes espacées (≤ 3/s sans clé) ; pas de moissonnage massif. "
        "Les résumés complets restent soumis aux droits des éditeurs."
    )
    VERSION = "1.0"
    STRATEGIE_MAJ = (
        "À la demande : chaque import interroge E-utilities "
        "(avec cache 24 h) et ajoute les publications manquantes."
    )

    VERSION_DONNEES = "E-utilities"

    # CGU NCBI : ≤ 3 requêtes/s sans clé — requêtes espacées.
    delai_minimum_s = 0.34

    def _params_base(self) -> dict:
        return {"tool": "WebIntel-recherche", "retmode": "json"}

    def _recherche_pmids(
        self, session: Session, requete: str, maximum: int
    ) -> list[str]:
        donnees = self.requete_json(
            session,
            f"{self.URL_SOURCE}/esearch.fcgi",
            {**self._params_base(), "db": "pubmed", "term": requete,
             "retmax": str(maximum)},
        )
        return (donnees.get("esearchresult") or {}).get("idlist", [])

    def _resume(self, session: Session, pmid: str) -> dict:
        donnees = self.requete_json(
            session,
            f"{self.URL_SOURCE}/esummary.fcgi",
            {**self._params_base(), "db": "pubmed", "id": pmid},
        )
        return (donnees.get("result") or {}).get(pmid, {})

    @staticmethod
    def _extraire_doi(resume: dict) -> str | None:
        for ident in resume.get("articleids", []):
            if ident.get("idtype") == "doi":
                return ident.get("value")
        return None

    @staticmethod
    def _extraire_annee(resume: dict) -> int | None:
        for cle in ("pubdate", "sortpubdate", "epubdate"):
            valeur = str(resume.get(cle) or "")
            if len(valeur) >= 4 and valeur[:4].isdigit():
                return int(valeur[:4])
        return None

    def tester_statut(self) -> tuple[bool, str]:
        """Teste E-utilities via une requête esearch minimale."""
        from webintel.core.exceptions import SecurityError
        from webintel.offline.reseau import MESSAGE_HORS_LIGNE, est_hors_ligne
        from webintel.security.pinned_transport import requete_sure

        if est_hors_ligne():
            return False, MESSAGE_HORS_LIGNE
        try:
            reponse = requete_sure(
                "GET",
                f"{self.URL_SOURCE}/esearch.fcgi",
                params={**self._params_base(), "db": "pubmed",
                        "term": "aspirin", "retmax": "1"},
                timeout=self.timeout_s,
                autoriser_hote_science=True,
                headers={"User-Agent": "WebIntel-recherche"},
            )
            reponse.raise_for_status()
            return True, "PubMed E-utilities joignable."
        except SecurityError as exc:
            return False, f"PubMed bloqué par la protection anti-SSRF : {exc}."
        except httpx.RequestError as exc:
            return False, f"PubMed injoignable : {exc}."
        except httpx.HTTPStatusError as exc:
            return False, f"PubMed répond en erreur : {exc}."

    def importer(
        self, session: Session, parametres: dict
    ) -> dict[str, Any]:
        """Importe des références PubMed pour une requête.

        Paramètres : ``{"requete": "<texte>", "max": <int>}``
        (``requete`` obligatoire, ``max`` défaut 5, plafond 20).

        Idempotence : sur PMID (puis DOI) — un réimport ne crée aucun
        doublon. Ne committe jamais.
        """
        params = parametres or {}
        requete = params.get("requete")
        if not requete or not str(requete).strip():
            raise ParametresInvalides(
                "Paramètre « requete » obligatoire : "
                'ex. {"requete": "Artemisia annua artemisinin"}.'
            )
        try:
            maximum = min(int(params.get("max", 5) or 5), _MAX_RESULTATS)
        except (TypeError, ValueError):
            raise ParametresInvalides(
                "Paramètre « max » invalide : entier attendu "
                f"(1 à {_MAX_RESULTATS})."
            )
        maximum = max(maximum, 1)

        rapport: dict[str, Any] = {
            "crees": 0,
            "mis_a_jour": 0,
            "ignores": 0,
            "erreurs": [],
            # Provenance standard (base) + clé historique version_donnees.
            "provenance": {
                **self.construire_provenance_import(
                    identifiant_externe=f"E-utilities:{requete}"
                ),
                "version_donnees": self.VERSION_DONNEES,
            },
        }

        pmids = self._recherche_pmids(
            session, str(requete).strip(), maximum
        )
        if not pmids:
            rapport["ignores"] = 1
            rapport["erreurs"].append(
                f"Aucune publication PubMed pour « {requete} »."
            )
            return rapport

        for pmid in pmids:
            try:
                resume = self._resume(session, pmid)
                doi = self._extraire_doi(resume)
                auteurs = [
                    a.get("name") for a in resume.get("authors", [])
                    if a.get("name")
                ]
                # Idempotence : DOI → PMID → titre normalisé (déduplication
                # partagée avec les publications locales — jamais de doublon).
                existante = trouver_evidence_existante(
                    session,
                    doi=doi,
                    pmid=pmid,
                    titre=resume.get("title") or "",
                    annee=self._extraire_annee(resume),
                )
                if existante is not None:
                    rapport["ignores"] += 1
                    continue
                preuve, _ = science_service.add_evidence(
                    session,
                    doi=doi,
                    source_type="publication",
                    document_type="article",
                    reference=f"PubMed PMID:{pmid}",
                    title=resume.get("title") or "",
                    authors=auteurs,
                    year=self._extraire_annee(resume),
                    pmid=pmid,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    retrieved_at=utcnow(),
                    provenance={
                        **self.construire_provenance_import(
                            identifiant_externe=f"PMID:{pmid}"
                        ),
                        "version_donnees": self.VERSION_DONNEES,
                    },
                )
                enregistrer_provenance(
                    session,
                    type_donnees="evidence",
                    item_id=preuve.id,
                    connecteur_nom=self.NOM,
                    version_donnees=self.VERSION_DONNEES,
                    licence=self.LICENCE,
                )
                rapport["crees"] += 1
            except Exception as exc:  # une fiche en échec n'arrête pas l'import
                rapport["erreurs"].append(
                    f"PMID {pmid} : {exc}"
                )
        return rapport
