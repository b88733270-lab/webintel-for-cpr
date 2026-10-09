"""Provider Wikipedia — sans clé (API REST MediaWiki)."""

from __future__ import annotations

from urllib.parse import quote

from webintel.providers.base import (
    BaseProvider,
    ProviderErreur,
    ResultatProvider,
    nettoyer_extrait,
)


class WikipediaProvider(BaseProvider):
    nom = "wikipedia"
    capacites = ["connaissances", "recherche_web"]
    necessite_cle = False
    hotes_autorises = frozenset({"fr.wikipedia.org", "en.wikipedia.org"})

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        lang = "en" if (langue or "fr").lower().startswith("en") else "fr"
        hote = f"{lang}.wikipedia.org"
        donnees = self._get_json(
            f"https://{hote}/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": requete,
                "srlimit": max(1, limite),
                "format": "json",
                "utf8": 1,
            },
            timeout_s=timeout_s,
        )
        resultats: list[ResultatProvider] = []
        try:
            lignes = donnees["query"]["search"]
        except (KeyError, TypeError) as exc:
            raise ProviderErreur(
                "Provider « wikipedia » : réponse inattendue (champ query.search absent). "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            ) from exc
        for ligne in lignes or []:
            titre = (ligne.get("title") or "").strip()
            if not titre:
                continue
            url = f"https://{hote}/wiki/{quote(titre.replace(' ', '_'), safe='_/()')}"
            resultats.append(
                ResultatProvider(
                    titre=titre,
                    url=url,
                    extrait=nettoyer_extrait(ligne.get("snippet")),
                    source=self.nom,
                    date=(ligne.get("timestamp") or None),
                    type="connaissances",
                )
            )
        return resultats
