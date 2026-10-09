"""Provider RSS actualités — sans clé (Google News RSS par défaut).

Le plus robuste sans clé testé pour WebIntel : Google News RSS ne
demande ni clé ni inscription et répond en RSS standard. Le flux est
configurable via le constructeur (autre flux RSS public possible),
mais l'URL par défaut est codée en dur — jamais saisie utilisateur.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

from webintel.providers.base import (
    BaseProvider,
    ProviderErreur,
    ResultatProvider,
    nettoyer_extrait,
)

FLUX_DEFAUT = "google-news"


class RssActualitesProvider(BaseProvider):
    nom = "rss_actualites"
    capacites = ["actualites", "recherche_web"]
    necessite_cle = False
    hotes_autorises = frozenset({"news.google.com"})

    def __init__(self, *, transport=None, flux: str = FLUX_DEFAUT) -> None:
        super().__init__(transport=transport)
        self._flux = flux

    def _url_flux(self, requete: str, langue: str) -> str:
        hl = "en" if (langue or "fr").lower().startswith("en") else "fr"
        gl = "US" if hl == "en" else "FR"
        ceid = "US:en" if hl == "en" else "FR:fr"
        return (
            f"https://news.google.com/rss/search?q={quote_plus(requete)}"
            f"&hl={hl}&gl={gl}&ceid={ceid}"
        )

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        xml_texte = self._get_texte(self._url_flux(requete, langue), timeout_s=timeout_s)
        try:
            racine = ET.fromstring(xml_texte)
        except ET.ParseError as exc:
            raise ProviderErreur(
                "Provider « rss_actualites » : flux RSS illisible (XML invalide). "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            ) from exc
        resultats: list[ResultatProvider] = []
        for item in racine.iter("item"):
            if len(resultats) >= limite:
                break
            titre = (item.findtext("title") or "").strip()
            lien = (item.findtext("link") or "").strip()
            if not titre or not lien:
                continue
            resultats.append(
                ResultatProvider(
                    titre=titre,
                    url=lien,
                    extrait=nettoyer_extrait(item.findtext("description")),
                    source=self.nom,
                    date=(item.findtext("pubDate") or None),
                    type="actualites",
                )
            )
        return resultats
