"""Provider DuckDuckGo Instant Answers — sans clé."""

from __future__ import annotations

from webintel.providers.base import (
    BaseProvider,
    ProviderErreur,
    ResultatProvider,
    nettoyer_extrait,
)


class DuckDuckGoProvider(BaseProvider):
    nom = "duckduckgo"
    capacites = ["recherche_web", "connaissances"]
    necessite_cle = False
    hotes_autorises = frozenset({"api.duckduckgo.com"})

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        donnees = self._get_json(
            "https://api.duckduckgo.com/",
            params={
                "q": requete,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
                "no_redirect": 1,
            },
            timeout_s=timeout_s,
        )
        if not isinstance(donnees, dict):
            raise ProviderErreur(
                "Provider « duckduckgo » : réponse inattendue. Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            )
        resultats: list[ResultatProvider] = []
        titre_principal = (donnees.get("Heading") or "").strip()
        resume = nettoyer_extrait(donnees.get("AbstractText"))
        url_resume = (donnees.get("AbstractURL") or "").strip()
        if titre_principal and url_resume:
            resultats.append(
                ResultatProvider(
                    titre=titre_principal,
                    url=url_resume,
                    extrait=resume,
                    source=self.nom,
                    date=None,
                    type="connaissances",
                )
            )
        # RelatedTopics : liste plate ou groupes imbriqués {Name, Topics[]}.
        for sujet in self._aplatir_sujets(donnees.get("RelatedTopics") or []):
            if len(resultats) >= limite:
                break
            url = (sujet.get("FirstURL") or "").strip()
            texte = nettoyer_extrait(sujet.get("Text"))
            if not url or not texte:
                continue
            titre = texte.split(" - ")[0].strip()[:200] or url
            resultats.append(
                ResultatProvider(
                    titre=titre,
                    url=url,
                    extrait=texte,
                    source=self.nom,
                    date=None,
                    type="web",
                )
            )
        return resultats[:limite]

    @staticmethod
    def _aplatir_sujets(sujets: list) -> list[dict]:
        plats: list[dict] = []
        for sujet in sujets:
            if not isinstance(sujet, dict):
                continue
            if isinstance(sujet.get("Topics"), list):
                plats.extend(DuckDuckGoProvider._aplatir_sujets(sujet["Topics"]))
            else:
                plats.append(sujet)
        return plats
