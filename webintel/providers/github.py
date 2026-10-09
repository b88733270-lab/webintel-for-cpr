"""Provider GitHub public — sans clé (token optionnel pour les quotas)."""

from __future__ import annotations

from webintel.config.settings import get_settings
from webintel.providers.base import (
    BaseProvider,
    ProviderErreur,
    ResultatProvider,
    nettoyer_extrait,
)


class GitHubProvider(BaseProvider):
    nom = "github"
    capacites = ["code", "recherche_web"]
    necessite_cle = False
    hotes_autorises = frozenset({"api.github.com"})

    def _cle(self) -> str:
        try:
            return (get_settings().WEBINTEL_GITHUB_TOKEN or "").strip()
        except Exception:
            return ""

    def rechercher(self, requete, *, limite=10, langue="fr", timeout_s=10.0):
        headers = {"Accept": "application/vnd.github+json"}
        token = self._cle()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        donnees = self._get_json(
            "https://api.github.com/search/repositories",
            params={"q": requete, "per_page": max(1, limite)},
            headers=headers,
            timeout_s=timeout_s,
        )
        if not isinstance(donnees, dict) or "items" not in donnees:
            raise ProviderErreur(
                "Provider « github » : réponse inattendue (champ items absent). "
                "Aucun résultat récupéré.",
                type_erreur="reponse_invalide",
            )
        resultats: list[ResultatProvider] = []
        for depot in donnees.get("items") or []:
            url = (depot.get("html_url") or "").strip()
            nom_complet = (depot.get("full_name") or "").strip()
            if not url or not nom_complet:
                continue
            description = nettoyer_extrait(depot.get("description"), limite=380)
            langage = (depot.get("language") or "").strip()
            etoiles = depot.get("stargazers_count")
            morceaux = [m for m in [description, f"Langage : {langage}" if langage else "",
                                     f"Étoiles : {etoiles}" if etoiles is not None else ""] if m]
            resultats.append(
                ResultatProvider(
                    titre=nom_complet,
                    url=url,
                    extrait=" — ".join(morceaux),
                    source=self.nom,
                    date=(depot.get("updated_at") or None),
                    type="code",
                )
            )
        return resultats
