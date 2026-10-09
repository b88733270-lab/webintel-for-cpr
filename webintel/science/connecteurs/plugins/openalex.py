"""Plugin OpenAlex — connecteur de source externe.

Source : OpenAlex (https://openalex.org), catalogue bibliographique
ouvert — métadonnées sous licence **CC0 (domaine public)**, sans clé
d'API, lecture seule. Le paramètre ``mailto`` identifie l'outil comme
recommandé par les CGU (file « polite »).

Licence vérifiée : https://openalex.org/legal — « All the data is
available under a CC0 license ».

Limites documentées :
- requêtes espacées (≤ 10/s en anonyme) ; pas de moissonnage massif ;
- import sélectif : une requête → au plus ``max`` travaux (défaut 5,
  plafond 20) ;
- chaque travail devient une ``Evidence`` idempotente (déduplication
  DOI → PMID → titre normalisé, partagée avec les publications
  locales) ;
- les textes intégraux restent soumis aux droits des éditeurs : seuls
  les métadonnées (titre, auteurs, année, DOI, PMID, source) sont
  importées.
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

_CHAMPS = (
    "id,doi,title,display_name,publication_year,authorships,"
    "primary_location,ids,type,language"
)

#: Correspondance type OpenAlex → type de document WebIntel.
_TYPES_DOCUMENT = {
    "article": "article",
    "review": "article",
    "letter": "article",
    "editorial": "article",
    "dissertation": "these",
    "thesis": "these",
    "report": "rapport",
    "dataset": "document_local",
    "book": "document_local",
    "book-chapter": "document_local",
    "other": "autre",
}


class OpenAlexConnecteur(BaseConnecteur):
    """Connecteur OpenAlex : métadonnées bibliographiques (CC0)."""

    NOM = "openalex"
    DESCRIPTION = (
        "OpenAlex : métadonnées bibliographiques ouvertes (titre, "
        "auteurs, année, DOI, PMID, revue) pour une requête — "
        "complémentaire de PubMed, sans clé d'API."
    )
    URL_SOURCE = "https://api.openalex.org"
    LICENCE = "OpenAlex — CC0 (données dans le domaine public)"
    LICENCE_URL = "https://openalex.org/legal"
    CONDITIONS_UTILISATION = (
        "Lecture seule, sans clé d'API ; paramètre mailto=WebIntel "
        "recommandé par les CGU. Requêtes espacées (≤ 10/s en anonyme) ; "
        "pas de moissonnage massif. Seules les métadonnées sont "
        "importées (les textes intégraux restent soumis aux droits "
        "des éditeurs)."
    )
    VERSION = "1.0"
    STRATEGIE_MAJ = (
        "À la demande : chaque import interroge l'API OpenAlex en "
        "direct (avec cache 24 h) et ajoute les références manquantes."
    )

    VERSION_DONNEES = "OpenAlex API"
    delai_minimum_s = 0.2

    def _params_base(self) -> dict:
        return {"mailto": "WebIntel-recherche", "select": _CHAMPS}

    def _rechercher(
        self, session: Session, requete: str, maximum: int
    ) -> list[dict]:
        donnees = self.requete_json(
            session,
            f"{self.URL_SOURCE}/works",
            {**self._params_base(), "search": requete, "per-page": str(maximum)},
        )
        return donnees.get("results", []) or []

    @staticmethod
    def _nettoyer_doi(doi: str | None) -> str | None:
        if not doi:
            return None
        doi = doi.strip()
        for prefixe in ("https://doi.org/", "http://doi.org/", "doi:"):
            if doi.lower().startswith(prefixe):
                return doi[len(prefixe):]
        return doi or None

    @staticmethod
    def _nettoyer_pmid(pmid: str | None) -> str | None:
        if not pmid:
            return None
        pmid = pmid.strip().rstrip("/")
        return pmid.rsplit("/", 1)[-1] or None

    @staticmethod
    def _auteurs(travail: dict) -> list[str]:
        auteurs = []
        for credit in travail.get("authorships", []) or []:
            nom = (credit.get("author") or {}).get("display_name")
            if nom:
                auteurs.append(nom)
        return auteurs

    def tester_statut(self) -> tuple[bool, str]:
        """Teste l'API OpenAlex via une requête minimale."""
        from webintel.core.exceptions import SecurityError
        from webintel.security.pinned_transport import requete_sure

        try:
            reponse = requete_sure(
                "GET",
                f"{self.URL_SOURCE}/works",
                params={**self._params_base(), "search": "test", "per-page": "1"},
                timeout=self.timeout_s,
                autoriser_hote_science=True,
                headers={"User-Agent": "WebIntel-recherche"},
            )
            reponse.raise_for_status()
            return True, "OpenAlex joignable."
        except SecurityError as exc:
            return False, f"OpenAlex bloqué par la protection anti-SSRF : {exc}."
        except httpx.RequestError as exc:
            return False, f"OpenAlex injoignable : {exc}."
        except httpx.HTTPStatusError as exc:
            return False, f"OpenAlex répond en erreur : {exc}."

    def importer(
        self, session: Session, parametres: dict
    ) -> dict[str, Any]:
        """Importe des références OpenAlex pour une requête.

        Paramètres : ``{"requete": "<texte>", "max": <int>}``
        (``requete`` obligatoire, ``max`` défaut 5, plafond 20).

        Idempotence : DOI → PMID → titre normalisé. Ne committe jamais.
        """
        params = parametres or {}
        requete = params.get("requete")
        if not requete or not str(requete).strip():
            raise ParametresInvalides(
                "Paramètre « requete » obligatoire : "
                "'ex. {\"requete\": \"Moringa oleifera flavonoids\"}.'"
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
            "provenance": {
                **self.construire_provenance_import(
                    identifiant_externe=f"OpenAlex:{requete}"
                ),
                "version_donnees": self.VERSION_DONNEES,
            },
        }

        travaux = self._rechercher(session, str(requete).strip(), maximum)
        if not travaux:
            rapport["ignores"] = 1
            rapport["erreurs"].append(
                f"Aucun travail OpenAlex pour « {requete} »."
            )
            return rapport

        for travail in travaux:
            try:
                doi = self._nettoyer_doi(travail.get("doi"))
                ids = travail.get("ids") or {}
                pmid = self._nettoyer_pmid(ids.get("pmid"))
                titre = travail.get("title") or travail.get("display_name") or ""
                annee = travail.get("publication_year")
                try:
                    annee = int(annee) if annee is not None else None
                except (TypeError, ValueError):
                    annee = None
                source = (
                    (travail.get("primary_location") or {}).get("source") or {}
                )
                revue = source.get("display_name")
                type_ouvert = (travail.get("type") or "").strip().lower()
                type_document = _TYPES_DOCUMENT.get(type_ouvert, type_ouvert or "article")
                openalex_id = (travail.get("id") or "").rsplit("/", 1)[-1]

                existante = trouver_evidence_existante(
                    session, doi=doi, pmid=pmid, titre=titre, annee=annee
                )
                if existante is not None:
                    rapport["ignores"] += 1
                    continue
                preuve, _ = science_service.add_evidence(
                    session,
                    doi=doi,
                    source_type="publication",
                    document_type=type_document,
                    reference=(
                        f"OpenAlex {openalex_id}" + (f" — {revue}" if revue else "")
                    ),
                    title=titre,
                    authors=self._auteurs(travail),
                    year=annee,
                    pmid=pmid,
                    url=(
                        f"https://doi.org/{doi}"
                        if doi
                        else (travail.get("id") or None)
                    ),
                    institution=revue,
                    langue=travail.get("language"),
                    retrieved_at=utcnow(),
                    provenance={
                        **self.construire_provenance_import(
                            identifiant_externe=f"OpenAlex:{openalex_id}"
                        ),
                        "version_donnees": self.VERSION_DONNEES,
                        "type_openalex": travail.get("type"),
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
                rapport["erreurs"].append(f"OpenAlex : {exc}")
        return rapport
