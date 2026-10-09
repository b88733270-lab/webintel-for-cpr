"""Interface commune des providers de recherche Web (v3.0.0).

Contrat d'un provider :

- ``nom`` : identifiant unique (ex. ``"wikipedia"``) ;
- ``capacites`` : capacités déclarées (``"recherche_web"``,
  ``"actualites"``, ``"connaissances"``, ``"code"``…) ;
- ``necessite_cle`` : vrai si une clé API est requise ;
- ``rechercher(requete, *, limite, langue, timeout_s)`` → liste de
  ``ResultatProvider`` (lève ``ProviderErreur`` en cas d'échec, jamais
  de faux résultat) ;
- ``statut()`` → dict d'état pour la page Paramètres (configuré ou non,
  SANS jamais exposer la clé).

Erreurs normalisées : ``ProviderErreur`` porte un ``type_erreur`` parmi
``timeout``, ``http``, ``quota``, ``non_configure``, ``reseau``,
``reponse_invalide`` et un message en français.

HTTP : les URL sont construites depuis des hôtes CODÉS EN DUR propres
à chaque provider — aucune URL fournie par l'utilisateur n'est jamais
requêtée ici. Avant chaque appel, l'hôte de l'URL construite est
vérifié contre l'hôte autorisé du provider (défense en profondeur,
dans l'esprit anti-SSRF de ``webintel/security/``, qui n'est pas
modifié). Aucune clé n'est journalisée, ni passée en URL quand un
en-tête est possible (SerpApi/NewsAPI imposent la clé en paramètre :
elle n'est alors jamais journalisée et jamais incluse dans les
messages d'erreur).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from webintel.core.logging import get_logger

logger = get_logger(__name__)

USER_AGENT = "WebIntel/3.0 (+https://webintel.local)"


class ProviderErreur(Exception):
    """Échec normalisé d'un provider (messages destinés à l'UI en français)."""

    TYPES = frozenset(
        {"timeout", "http", "quota", "non_configure", "reseau", "reponse_invalide"}
    )

    def __init__(self, message: str, *, type_erreur: str = "http") -> None:
        super().__init__(message)
        self.type_erreur = type_erreur if type_erreur in self.TYPES else "http"


@dataclass
class ResultatProvider:
    """Un résultat de recherche normalisé, quel que soit le provider."""

    titre: str
    url: str
    extrait: str = ""
    source: str = ""
    date: str | None = None
    type: str = "web"

    def vers_dict(self) -> dict[str, Any]:
        return {
            "titre": self.titre,
            "url": self.url,
            "extrait": self.extrait,
            "source": self.source,
            "date": self.date,
            "type": self.type,
        }


_TAG_HTML_RE = re.compile(r"<[^>]+>")
_ESPACES_RE = re.compile(r"\s+")


def nettoyer_extrait(texte: str | None, *, limite: int = 500) -> str:
    """Retire le HTML d'un extrait, décode ses entités et borne sa longueur.

    Les extraits Wikipedia (et plusieurs flux RSS) arrivent avec du HTML
    (``<span class="searchmatch">…``) et des entités (``&#039;``, ``&amp;``) :
    sans décodage, l'interface affichait littéralement « &#039; » au milieu
    des phrases. On retire d'abord les balises, puis on décode les entités
    une seule fois, puis on normalise les espaces.
    """
    if not texte:
        return ""
    sans_balises = _TAG_HTML_RE.sub(" ", str(texte))
    decode = html.unescape(sans_balises)
    propre = _ESPACES_RE.sub(" ", decode).strip()
    if len(propre) > limite:
        return propre[: limite - 1].rstrip() + "…"
    return propre


class BaseProvider:
    """Classe de base de tout provider de recherche Web."""

    nom: str = ""
    capacites: list[str] = []
    necessite_cle: bool = False
    #: Hôte(s) autorisé(s) pour ce provider — les URL construites ne
    #: peuvent pointer que vers ces hôtes (codés en dur, jamais saisis).
    hotes_autorises: frozenset[str] = frozenset()

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        # Transport injectable : les tests passent un MockTransport
        # (aucun réseau réel) ; en production, transport réel par défaut.
        self._transport = transport

    # ------------------------------------------------------------------
    # Contrat à implémenter
    # ------------------------------------------------------------------
    def rechercher(
        self,
        requete: str,
        *,
        limite: int = 10,
        langue: str = "fr",
        timeout_s: float = 10.0,
    ) -> list[ResultatProvider]:
        raise NotImplementedError

    def statut(self) -> dict[str, Any]:
        """État du provider pour la page Paramètres (jamais la clé)."""
        if self.necessite_cle and not self._cle():
            return {
                "nom": self.nom,
                "capacites": list(self.capacites),
                "necessite_cle": True,
                "configure": False,
                "statut": "non_configure",
                "message": (
                    f"Clé API absente : posez la clé de « {self.nom} » dans "
                    "le fichier .env à la racine du projet (voir .env.example), "
                    "puis redémarrez. Le provider est sauté en attendant."
                ),
            }
        detail = "prêt (sans clé)" if not self.necessite_cle else "prêt"
        if self.nom == "github":
            detail = (
                "prêt (avec token GitHub)"
                if self._cle()
                else "prêt (sans token — quotas publics réduits)"
            )
        return {
            "nom": self.nom,
            "capacites": list(self.capacites),
            "necessite_cle": self.necessite_cle,
            "configure": True,
            "statut": "pret",
            "message": detail,
        }

    # ------------------------------------------------------------------
    # Clé (surchargeable) — jamais journalisée, jamais exposée
    # ------------------------------------------------------------------
    def _cle(self) -> str:
        """Clé API du provider (``""`` si absente). À surcharger."""
        return ""

    def _exiger_cle(self) -> str:
        cle = self._cle()
        if self.necessite_cle and not cle:
            raise ProviderErreur(
                f"Provider « {self.nom} » non configuré : clé API absente. "
                "Posez-la dans le fichier .env à la racine du projet "
                "(voir .env.example), puis redémarrez.",
                type_erreur="non_configure",
            )
        return cle

    # ------------------------------------------------------------------
    # HTTP discipliné (hôtes codés en dur uniquement)
    # ------------------------------------------------------------------
    def _verifier_hote(self, url: str) -> None:
        hote = (urlparse(url).hostname or "").lower().rstrip(".")
        if self.hotes_autorises and hote not in self.hotes_autorises:
            raise ProviderErreur(
                f"Provider « {self.nom} » : hôte « {hote} » non autorisé "
                "(URL interne incohérente — requête annulée).",
                type_erreur="http",
            )

    def _client(self, timeout_s: float, *, proxy: str | None = None) -> httpx.Client:
        # trust_env=False : même discipline que client_http_sure — httpx
        # plante sur certaines variables d'environnement (no_proxy IPv6)
        # quand il les parse lui-même ; le proxy, quand il est utilisé,
        # est lu explicitement et borné (voir _proxy_environnement).
        # verify=contexte système : le magasin de certificats de l'OS est
        # honoré (comportement curl/navigateur) — indispensable derrière
        # un proxy d'entreprise qui intercepte TLS avec sa propre CA ;
        # la vérification du nom d'hôte d'origine reste active.
        import ssl

        return httpx.Client(
            transport=self._transport,
            timeout=httpx.Timeout(timeout_s),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            trust_env=False,
            proxy=proxy,
            verify=ssl.create_default_context(),
        )

    @staticmethod
    def _proxy_environnement() -> str | None:
        """Proxy HTTP(S) explicite de l'environnement, ou ``None``.

        Lu directement (jamais via ``trust_env``) : seules les variables
        standard sont honorées, et l'URL du proxy n'est JAMAIS
        journalisée (elle peut contenir des identifiants).
        """
        import os

        for nom in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            valeur = (os.environ.get(nom) or "").strip()
            if valeur:
                return valeur
        return None

    def _requete(self, methode: str, url: str, *, timeout_s: float, **kwargs) -> httpx.Response:
        """Requête HTTP bornée : direct d'abord, repli proxy borné ensuite.

        Le repli (même esprit que les connecteurs scientifiques) ne
        s'applique que si la connexion directe échoue, qu'un proxy est
        configuré, que le schéma est https et que l'hôte — déjà vérifié
        contre les hôtes codés en dur du provider — est la cible : le
        proxy résout alors le nom (postes derrière proxy « fake-IP »),
        le certificat reste vérifié sur le nom d'origine et aucune URL
        utilisateur n'est jamais en jeu.
        """
        try:
            with self._client(timeout_s) as client:
                return client.request(methode, url, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as erreur_directe:
            proxy = self._proxy_environnement()
            if self._transport is not None or not proxy or not url.startswith("https://"):
                raise
            logger.warning(
                "Provider « %s » : connexion directe impossible — second essai via le proxy configuré (hôte %s).",
                self.nom, (urlparse(url).hostname or "?"),
            )
            try:
                with self._client(timeout_s, proxy=proxy) as client:
                    return client.request(methode, url, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                raise erreur_directe

    def _normaliser_erreur_http(self, exc: Exception) -> ProviderErreur:
        """Convertit une exception httpx en ``ProviderErreur`` (sans clé)."""
        if isinstance(exc, httpx.TimeoutException):
            return ProviderErreur(
                f"Provider « {self.nom} » : délai dépassé, aucune réponse "
                "dans le temps imparti. Aucun résultat récupéré.",
                type_erreur="timeout",
            )
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code if exc.response is not None else "?"
            if code == 429:
                return ProviderErreur(
                    f"Provider « {self.nom} » : quota dépassé (HTTP 429). "
                    "Réessayez plus tard ou ajoutez une clé API.",
                    type_erreur="quota",
                )
            return ProviderErreur(
                f"Provider « {self.nom} » : réponse HTTP {code}. "
                "Aucun résultat récupéré.",
                type_erreur="http",
            )
        if isinstance(exc, httpx.RequestError):
            return ProviderErreur(
                f"Provider « {self.nom} » : erreur réseau ({type(exc).__name__}). "
                "Vérifiez la connexion — aucun résultat récupéré.",
                type_erreur="reseau",
            )
        return ProviderErreur(
            f"Provider « {self.nom} » : erreur inattendue ({type(exc).__name__}). "
            "Aucun résultat récupéré.",
            type_erreur="http",
        )

    def _get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float = 10.0,
    ) -> Any:
        self._verifier_hote(url)
        try:
            reponse = self._requete("GET", url, params=params, headers=headers, timeout_s=timeout_s)
            reponse.raise_for_status()
            return reponse.json()
        except ProviderErreur:
            raise
        except Exception as exc:  # httpx + JSON invalide
            if isinstance(exc, ValueError):
                raise ProviderErreur(
                    f"Provider « {self.nom} » : réponse illisible (JSON invalide). "
                    "Aucun résultat récupéré.",
                    type_erreur="reponse_invalide",
                ) from exc
            raise self._normaliser_erreur_http(exc) from exc

    def _post_json(
        self,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float = 10.0,
    ) -> Any:
        self._verifier_hote(url)
        try:
            reponse = self._requete("POST", url, json=json_body, headers=headers, timeout_s=timeout_s)
            reponse.raise_for_status()
            return reponse.json()
        except ProviderErreur:
            raise
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise ProviderErreur(
                    f"Provider « {self.nom} » : réponse illisible (JSON invalide). "
                    "Aucun résultat récupéré.",
                    type_erreur="reponse_invalide",
                ) from exc
            raise self._normaliser_erreur_http(exc) from exc

    def _get_texte(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float = 10.0,
    ) -> str:
        self._verifier_hote(url)
        try:
            reponse = self._requete("GET", url, params=params, headers=headers, timeout_s=timeout_s)
            reponse.raise_for_status()
            return reponse.text
        except ProviderErreur:
            raise
        except Exception as exc:
            raise self._normaliser_erreur_http(exc) from exc
